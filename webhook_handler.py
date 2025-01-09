import os
import aiohttp
import discord
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

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

def add_email_to_db(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(1) FROM used_emails WHERE email = %s", (email,))
        result = cursor.fetchone()
        if result[0] > 0:
            print(f"E-mail {email} já existe no banco de dados.")
            return
        
        cursor.execute("INSERT INTO used_emails (email) VALUES (%s)", (email,))
        conn.commit()
        print(f"E-mail {email} adicionado ao banco de dados.")
    except mysql.connector.Error as err:
        print(f"Erro ao adicionar e-mail {email}: {err}")
    finally:
        cursor.close()
        conn.close()


# Função para remover um e-mail do banco de dados
def remove_email_from_db(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM used_emails WHERE email = %s", (email,))
        conn.commit()
        print(f"E-mail {email} reembolsado e removido do banco de dados.")
    except mysql.connector.Error as err:
        print(f"Erro ao remover e-mail {email}: {err}")
    finally:
        cursor.close()
        conn.close()


# Função para lidar com o webhook de reembolso
async def handle_refund(webhook_data, bot):
    # Verifique se os dados necessários estão presentes
    if not webhook_data or 'customer' not in webhook_data:
        print("Webhook mal formatado ou dados ausentes.")
        return

    # Obtenha o email diretamente de 'customer'
    email = webhook_data['customer'].get('email')

    if not email:
        print("Webhook recebido sem e-mail.")
        return
    
    # Verificar se o portador do e-mail pediu reembolso com a API
    is_refunded = await fetch_refund_status(email)
    if is_refunded:
        # Remover o e-mail do banco de dados caso o reembolso tenha sido efetuado
        remove_email_from_db(email)
        
        # Obter o user_id do banco de dados
        from main import get_user_id_from_db
        user_id = get_user_id_from_db(email)
        if not user_id:
            # Se o e-mail não foi encontrado no banco de dados, ignorar
            return print("E-mail não foi encontrado no banco de dados, não foi necessário remoção do cargo no Discord.")

        # Remover o cargo do usuário no servidor Discord
        guild = bot.get_guild(GUILD_ID)
        member = discord.utils.get(guild.members, id=user_id)

        if member:
            role = discord.utils.get(guild.roles, id=NOBRES_ROLE_ID)
            if role in member.roles:
                await member.remove_roles(role)
                await member.send("Seu cargo foi removido porque seu pedido foi reembolsado com sucesso.")
                print(f"Cargo removido de {member.display_name}.")
        else:
            print("Usuário não encontrado no servidor.")
    else:
        print("Reembolso não confirmado para o e-mail:", email)

# Função para buscar o status do reembolso na API da Ticto
async def fetch_refund_status(email):
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
                return False
            access_token = (await auth_response.json())["access_token"]

        # Verificar o status do reembolso
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "filter[products]": ",".join(TICTO_PRODUCT_IDS),  # IDs dos produtos
            "filter[status]": "refunded",  # Filtro de status
            "filter[customerNameOrEmail]": email  # Filtro pelo email
        }
        async with session.get(TICTO_ORDERS_URL, headers=headers, params=params) as response:
            if response.status != 200:
                print("Erro ao buscar status do reembolso.")
                return False
            data = await response.json()
            # Verificando se a transação do pedido está reembolsada
            if data.get("data"):  # Verificando se há dados
                order = data["data"][0]  # Considerando que retorna apenas um pedido
                transaction_status = order.get("transaction", {}).get("status")
                if transaction_status == "refunded":
                    return True
    return False