from flask import Flask, request, Response

app = Flask(__name__)

@app.route('/update_node_directory', methods=['POST'])

def return_response():
    print("Updating _ot._udp service hostnames...")
    with open("/home/yannpi/homeassistant/config/custom_components/ha-coap-integration/scripts/update-node-directory.py") as f:
        exec(f.read())
    return Response(status=200)

if __name__ == "__main__": app.run()