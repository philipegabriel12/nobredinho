import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import aiohttp
from pyngrok import ngrok
from http.server import BaseHTTPRequestHandler, HTTPServer
import mysql.connector
from mysql.connector import pooling
import threading
import asyncio
from datetime import datetime
from webhook_handler import handle_refund
import json

# Carregar variáveis de ambiente
load_dotenv()
TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
NOBRES_ROLE_ID = int(os.getenv("NOBRES_ROLE_ID"))
VIT_ROLE_ID = int(os.getenv("VIT_ROLE_ID"))
TICTO_CLIENT_ID = os.getenv("TICTO_CLIENT_ID")
TICTO_CLIENT_SECRET = os.getenv("TICTO_CLIENT_SECRET")
TICTO_OAUTH_URL = os.getenv("TICTO_OAUTH_URL")
TICTO_ORDERS_URL = os.getenv("TICTO_ORDERS_URL")
TICTO_SUBSCRIPTIONS_URL = os.getenv("TICTO_SUBSCRIPTIONS_URL")
NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN")
TICTO_PRODUCT_IDS = ["ID DO PRODUTO TICTO"]
TUNNEL_DOMAIN = os.getenv("TUNNEL_DOMAIN")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT"))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DATABASE = os.getenv("DB_DATABASE")

# Criando um pool de conexões para MySQL
dbconfig = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_DATABASE
}

connection_pool = pooling.MySQLConnectionPool(pool_name="mypool", pool_size=5, **dbconfig)

def get_db_connection():
    return connection_pool.get_connection()

# Debug function para ver o que tem dentro da DB
def debug_show_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM used_emails")
        results = cursor.fetchall()

        if results:
            print("Conteúdo da tabela 'used_emails':")
            for row in results:
                print(f"Email: {row[0]}, User ID: {row[1]}, Subscription: {row[2]}, Expiration Date: {row[3]}")
        else:
            print("A tabela 'used_emails' está vazia.")
    except mysql.connector.Error as err:
        print(f"Erro ao exibir a tabela: {err}")
    finally:
        cursor.close()
        conn.close()

# Debug function para excluir entradas da database (chamar apenas em testes ou depurações)
def debug_clear_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM used_emails")
        conn.commit()
        print("Todas as entradas da tabela 'used_emails' foram apagadas com sucesso.")
    except mysql.connector.Error as err:
        print(f"Erro ao apagar entradas da tabela: {err}")
    finally:
        cursor.close()
        conn.close()

# JAMAIS CHAMAR ESSA POHA EM PRODUÇÃO
# debug_clear_database()

# Debug Function para criar a table used_emails
def create_table_if_not_exists():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Criação da tabela caso não exista
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS used_emails (
                email VARCHAR(255) NOT NULL,
                user_id VARCHAR(50) NOT NULL,
                subscription VARCHAR(20) NOT NULL,
                expiration_date DATETIME,
                PRIMARY KEY (email)
            )
        """)
        print("Tabela 'used_emails' verificada/criada com sucesso.")

        # Verificar se a coluna 'expiration_date' já existe
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'used_emails' AND COLUMN_NAME = 'expiration_date'
        """)
        result = cursor.fetchone()

        # Se a coluna não existir, alteramos a tabela para adicioná-la
        if not result:
            cursor.execute("""
                ALTER TABLE used_emails 
                ADD COLUMN expiration_date DATETIME
            """)
            print("Coluna 'expiration_date' adicionada com sucesso.")

    except mysql.connector.Error as err:
        print(f"Erro ao criar/verificar tabela: {err}")
    finally:
        cursor.close()
        conn.close()

# create_table_if_not_exists()
# print("Executada a função create_table_if_not_exists()")

# Função para verificar se o e-mail já foi utilizado (consultando o banco de dados)
def check_email_in_db(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM used_emails WHERE email = %s", (email,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result is not None

# Função para adicionar o e-mail, user_id e subscription no banco de dados
def save_used_email(email, user_id, subscription):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO used_emails (email, user_id, subscription)
            VALUES (%s, %s, %s)
        """, (email, user_id, subscription))
        conn.commit()
    except mysql.connector.Error as err:
        print(f"Erro ao adicionar e-mail {email} no banco de dados: {err}")
    finally:
        cursor.close()
        conn.close()

# Inicializar o bot
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Habilitar o conteúdo das mensagens
bot = commands.Bot(command_prefix="/", intents=intents)

# Bot Start
@bot.event
async def on_ready():
    print(f'Logado como {bot.user} (ID: {bot.user.id})')
    guild = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild)
        print(f"Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Erro ao sincronizar os comandos: {e}")

    # Activity Status
    await bot.change_presence(activity=discord.Game(name="Ajudando os nobres!"))

    # Iniciar verificação agendada
    bot.loop.create_task(scheduled_subscription_check())

    # Restart Protection, loads pending warns in case bot shutdowns
    target_guild = bot.get_guild(GUILD_ID)
    
    if target_guild is None:
        print(f"Target guild (ID: {GUILD_ID}) not found.")
        return

# Comando de verificação
@bot.tree.command(name="verificar", guild=discord.Object(id=GUILD_ID), description="Verifique sua conta do Discord com o e-mail da compra.")
async def verificar(interaction: discord.Interaction):
    # Enviar mensagem informando que o usuário deve verificar a DM
    await interaction.response.send_message("Para prosseguir com a verificação, te enviei uma mensagem no seu privado (DM).", ephemeral=True)

    # Enviar uma mensagem direta (DM) pedindo o e-mail
    await interaction.user.send("Olá! Para verificar sua conta, por favor, me envie o seu e-mail da compra.")

    def check(m):
        return m.author == interaction.user and isinstance(m.channel, discord.DMChannel)

    try:
        # Aguarda o e-mail ser enviado pelo usuário via DM
        msg = await bot.wait_for('message', check=check, timeout=60)
        email = msg.content.strip()  # E-mail fornecido pelo usuário

        # Verificar se o e-mail está associado a um pedido
        async with aiohttp.ClientSession() as session:
            # Obter token de acesso da API Ticto
            auth_data = {
                "grant_type": "client_credentials",
                "client_id": TICTO_CLIENT_ID,
                "client_secret": TICTO_CLIENT_SECRET
            }
            async with session.post(TICTO_OAUTH_URL, json=auth_data) as auth_response:
                if auth_response.status != 200:
                    await interaction.user.send("Erro ao obter token de autenticação.")
                    return
                access_token = (await auth_response.json())["access_token"]

            # Buscar pedidos com os filtros necessários
            headers = {"Authorization": f"Bearer {access_token}"}
            params = {
                "filter[products]": ",".join(TICTO_PRODUCT_IDS),  # IDs dos produtos
                "filter[customerNameOrEmail]": email,  # E-mail fornecido pelo usuário
                "filter[status]": "authorized"  # Apenas vendas autorizadas #debug error
            }
            async with session.get(TICTO_ORDERS_URL, headers=headers, params=params) as orders_response:
                if orders_response.status != 200:
                    await interaction.user.send("Erro interno ao buscar o seu pedido, tente novamente mais tarde.")
                    return
                orders = (await orders_response.json()).get("data", [])

            # Se não houver pedidos para o e-mail, informar o usuário
            
            if not orders:
                await interaction.user.send("Este e-mail não está associado a nenhum dos nossos produtos.")
                return

            # Obter o ID da oferta do primeiro pedido
            order = orders[0]
            subscription_id = order.get("offer", {}).get("id")

            # Definir o tipo de assinatura com base no ID da oferta
            if subscription_id == 107387:
                subscription = "30d"
            elif subscription_id == 107389:
                subscription = "365d"
            else:
                subscription = "inf"

            # Verificar se o e-mail já foi utilizado no banco de dados
            if check_email_in_db(email):
                await interaction.user.send("Este e-mail já foi utilizado para verificação e não pode ser mais utilizado.")
                return

            # Salvar o e-mail na tabela 'used_emails' e atribuir o cargo ao usuário
            save_used_email(email, interaction.user.id, subscription)
            guild = bot.get_guild(GUILD_ID)
            member = guild.get_member(interaction.user.id)
            role = guild.get_role(NOBRES_ROLE_ID)
            role_vit = guild.get_role(VIT_ROLE_ID)
            if member and role:
                if subscription == "inf":
                    await member.add_roles(role)
                    await member.add_roles(role_vit)
                    await interaction.user.send("Seja muito bem-vindo(a) meu Nobre! Você agora faz parte da Comunidade Nobredim para sempre! :sunglasses:")
                else:
                    await member.add_roles(role)
                    await interaction.user.send("Você verificou o seu e-mail com sucesso! Seja muito bem-vindo à Comunidade Nobredim! :sunglasses:")
            else:
                await interaction.user.send("Erro ao adicionar o cargo. O bot não tem as permissões necessárias.")


    except asyncio.TimeoutError:
        await interaction.user.send("Você demorou muito para responder. Tente novamente mais tarde.")


#
# parte de renovação da assinatura
#

async def check_subscription_status():
    async with aiohttp.ClientSession() as session:
        # Obter token de acesso da API Ticto
        auth_data = {
            "grant_type": "client_credentials",
            "client_id": TICTO_CLIENT_ID,
            "client_secret": TICTO_CLIENT_SECRET
        }
        async with session.post(TICTO_OAUTH_URL, json=auth_data) as auth_response:
            if auth_response.status != 200:
                print("Erro ao obter token de autenticação.")
                return
            access_token = (await auth_response.json())["access_token"]

        # Buscar as assinaturas filtradas pelo status ativo
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"filter[situation]": "active"}
        async with session.get(TICTO_SUBSCRIPTIONS_URL, headers=headers, params=params) as response:
            if response.status != 200:
                print("Erro ao buscar assinaturas.")
                return
            subscriptions = (await response.json()).get("data", [])

            for subscription in subscriptions:
                email = subscription.get("customer", {}).get("email")
                situation = subscription.get("situation")
                next_charge = subscription.get("next_charge")

                # Verifica se o e-mail já foi usado no comando /verificar
                user_id = get_user_id_from_db(email)
                if not user_id:
                    # Se o e-mail não foi encontrado no banco de dados, ignorar
                    continue

                # Validar se a assinatura está ativa
                if situation == "Ativa":
                    next_charge_date = datetime.strptime(next_charge, "%Y-%m-%dT%H:%M:%S.%fZ")

                    # Verifica se a data de renovação passou e se o usuário renovou
                    if datetime.now() > next_charge_date:
                        await handle_expired_subscription(user_id, email)
                    else:
                        await handle_renewed_subscription(user_id, next_charge_date)
                else:
                    # Se a assinatura está cancelada ou atrasada, proceder com a expiração
                    await handle_expired_subscription(user_id, email)


# quando a assinatura for renovada

async def handle_renewed_subscription(user_id, next_charge_date):
    # Atualiza a data de expiração no banco de dados
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE used_emails
        SET expiration_date = %s
        WHERE user_id = %s
    """, (next_charge_date, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    # Notificar o usuário
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    if member:
        await member.send(f"Sua assinatura da Comunidade Nobredim foi renovada com sucesso. Novo prazo: {next_charge_date.strftime('%d/%m/%Y')}")

# quando a assinatura for expirada

async def handle_expired_subscription(user_id, email):
    # Remover o cargo e notificar o usuário
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    role = guild.get_role(NOBRES_ROLE_ID)
    if member and role:
        await member.remove_roles(role)
        await member.send("Seu acesso à comunidade expirou. Por favor, renove sua assinatura no canal de autenticação para obter acesso novamente.")
    
    # Remover o e-mail da tabela "used_emails"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM used_emails WHERE email = %s", (email,))
    conn.commit()
    cursor.close()
    conn.close()

# pega o ID do usuário do discord da DB

def get_user_id_from_db(email: str) -> str:
    try:
        # Obtém a conexão do banco de dados
        conn = get_db_connection()  
        cursor = conn.cursor()

        # Busca o Discord user ID pelo email
        query = "SELECT user_id FROM used_emails WHERE email = %s"
        cursor.execute(query, (email,))
        result = cursor.fetchone()

        # Fecha o cursor e a conexão
        cursor.close()
        conn.close()

        # Se encontrar o resultado, retorna o user_id
        if result:
            return result[0]
        else:
            return None
    except Exception as e:
        print(f"Erro ao buscar user_id no banco de dados: {e}")
        return None
    
# por fim, a função da verificação agendada
async def scheduled_subscription_check():
    while True:
        await check_subscription_status()
        await asyncio.sleep(86400)  # Esperar 24 horas antes de verificar novamente (86400 segundos)

#
# fim da verificação da assinatura
#

# Definir o domínio ngrok fixo

ngrok.set_auth_token(NGROK_AUTH_TOKEN)
tunnel = ngrok.connect(addr=8000, bind_tls=True, domain=TUNNEL_DOMAIN)
public_url = tunnel.public_url

# Adicionar cabeçalhos personalizados para evitar a página de aviso

async def handle_ngrok_warning(request, response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# Iniciar um servidor HTTP básico para escutar o webhook - executar o webhook_handler.
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])  # Recebe o tamanho do corpo da requisição
        post_data = self.rfile.read(content_length)  # Lê o conteúdo do POST

        # Decodifica os dados para string
        post_data_str = post_data.decode('utf-8')

        # Converte a string para um objeto JSON
        try:
            webhook_data = json.loads(post_data_str)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Erro ao processar JSON.")
            return

        # Agora, passe os dados para a função handle_refund
        asyncio.run(self.handle_refund(webhook_data))

        self.send_response(200)
        self.end_headers()

    async def handle_refund(self, webhook_data):
        # Supondo que handle_refund seja uma função assíncrona externa:
        await handle_refund(webhook_data, bot)
        print("Operação concluída.")

def start_webhook_server():
    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    print("Servidor HTTP iniciado na porta 8000.")
    httpd.serve_forever()

# Iniciar o servidor HTTP em uma thread separada
threading.Thread(target=start_webhook_server, daemon=True).start()

# Iniciar o bot
bot.run(TOKEN)