from zeroconf import ServiceBrowser, Zeroconf
import time
import sys

class MyListener:
    def __init__(self, output_file):
        open(output_file, 'w')
        self.output_file = output_file

    def remove_service(self, zeroconf, type, name):
        pass
                                    
    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            with open(self.output_file, 'a') as file:
                file.write(f"Hostname: {info.server}\n")
                file.write(f"IPv6 Address: {info.parsed_addresses()[0]}\n")
                file.write("=" * 30 + "\n")
    
    def update_service(self, zc: "zeroconf.Zeroconf", type_: str, name: str) -> None:
        pass

output_file = "node_directory.txt"
zeroconf = Zeroconf()
listener = MyListener(output_file)

# Change '_services._dns-sd._udp.local.' to the service type you want to discover
browser = ServiceBrowser(zeroconf, '_ot._udp.local.', listener)

try:
    time.sleep(1)
finally:
    zeroconf.close()
    sys.exit()  # Terminate the script