import pika
from pika.credentials import PlainCredentials
import os
import sys
import time

# Добавляем путь к common модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.logging_config import setup_logging

# === Настройки из переменных окружения ===
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest123")

# Настройка логирования
logger = setup_logging("notification-service")

# === Функция подключения с ретраем ===
def connect():
    credentials = PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    attempt = 1
    while True:
        try:
            logger.info(f"Connecting to RabbitMQ (attempt {attempt})...")
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
            )
            logger.info("Connected to RabbitMQ successfully")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            logger.warning(f"RabbitMQ not ready ({e}). Retrying in 5s...")
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
        logger.info(f"Received event from {ex}: {body.decode()}")

    channel.basic_consume(queue=queue_name, on_message_callback=handle_event, auto_ack=True)

# Прямая очередь для уведомлений
channel.queue_declare(queue='notifications', durable=True)

def handle_notify(ch, method, properties, body):
    logger.info(f"Notification received: {body.decode()}")

channel.basic_consume(queue='notifications', on_message_callback=handle_notify, auto_ack=True)

logger.info("Notification Service started, waiting for messages...")
channel.start_consuming()
