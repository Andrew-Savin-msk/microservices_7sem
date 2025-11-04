import pika
from pika.credentials import PlainCredentials
import os
import time

# === Настройки из переменных окружения ===
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest123")

# === Функция подключения с ретраем ===
def connect():
    credentials = PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    attempt = 1
    while True:
        try:
            print(f"[NOTIFICATION] Connecting to RabbitMQ (attempt {attempt})...")
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
            )
            print("[NOTIFICATION] Connected to RabbitMQ ✅")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            print(f"[NOTIFICATION] RabbitMQ not ready ({e}). Retrying in 5s...")
            attempt += 1
            time.sleep(5)

# === Подключение и создание канала ===
connection = connect()
channel = connection.channel()

# === Настройка exchange'ов и очередей ===
exchanges = ['catalog_events', 'payment_events']
for ex in exchanges:
    channel.exchange_declare(exchange=ex, exchange_type='fanout')
    result = channel.queue_declare(queue='', exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(exchange=ex, queue=queue_name)

    # Обработчик для fanout
    def handle_event(ch, method, properties, body, ex=ex):
        print(f"[{ex.upper()} EVENT] {body.decode()}")

    channel.basic_consume(queue=queue_name, on_message_callback=handle_event, auto_ack=True)

# Прямая очередь для уведомлений
channel.queue_declare(queue='notifications', durable=True)

def handle_notify(ch, method, properties, body):
    print(f"[NOTIFY] {body.decode()}")

channel.basic_consume(queue='notifications', on_message_callback=handle_notify, auto_ack=True)

print("[NOTIFICATION SERVICE] Waiting for messages...")
channel.start_consuming()
