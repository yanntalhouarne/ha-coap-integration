import requests

WebhookURL = 'http://127.0.0.1:5000/update_node_directory'

r = requests.post(WebhookURL)