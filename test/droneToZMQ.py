#!/usr/bin/env python3

# Script to manually send JSON over ZMQ

import json
import zmq
import sys
import time
from importlib import reload
from threading import Thread
import drone_lib

class Drone:
    """Represents a drone and its telemetry data."""
    def __init__(self, id: str, lat: float, lon: float, speed: float, vspeed: float, alt: float, height: float, pilot_lat: float, pilot_lon: float, description: str):
        self.id = id
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.vspeed = vspeed
        self.alt = alt
        self.height = height
        self.pilot_lat = pilot_lat
        self.pilot_lon = pilot_lon
        self.description = description

def decode_coord(data):
        if data == 0:
            return "Unknown"
        else:
            return "%.7f" % (data / 10 ** 7)

def main():
    url = f"tcp://127.0.0.1:2402"

    context = zmq.Context()
    socket = context.socket(zmq.XPUB)
    socket.setsockopt(zmq.XPUB_VERBOSE, True)
    socket.bind(url)

    def zmq_thread(socket):
        try:
            while True:
                event = socket.recv()
                # Event is one byte 0=unsub or 1=sub, followed by topic
                if event[0] == 1:
                    log("new subscriber for", event[1:])
                elif event[0] == 0:
                    log("unsubscribed", event[1:])
        except zmq.error.ContextTerminated:
            pass

    def log(*msg):
        s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        print("%s:" % s, *msg, end="\n", file=sys.stderr)

    zthread = Thread(target=zmq_thread, args=[socket], daemon=True, name='zmq')
    zthread.start()

    while True:
        try:
            with open('drone_send.json', 'r') as json_file:
                msg = json.load(json_file)
                run_num = 0
                while run_num < 3:
                    for item in msg[0][4]["Location Vector"]["coord"]:
                        if item == "latitude":
                            msg[0][4]["Location Vector"]["coord"]["latitude"] = decode_coord(float(drone_lib.lat_list[run_num]))
                        if item == "longitude":
                            msg[0][4]["Location Vector"]["coord"]["longitude"] = decode_coord(float(drone_lib.lon_list[run_num]))
                    for item in msg[0][2]["Basic ID"]:
                        if item == "id":
                            msg[0][2]["Basic ID"]["id"] = drone_lib.id_num[run_num]
                    print(msg[0])
                    print()
                    socket.send_string(json.dumps(msg[0]))
                    run_num += 1
                    if run_num == 3:
                        reload(drone_lib)
                        run_num == 0
                    time.sleep(1)

        except KeyboardInterrupt:
            socket.close()
            sys.stderr.write("\r")
            break

if __name__ == "__main__":
    main()
