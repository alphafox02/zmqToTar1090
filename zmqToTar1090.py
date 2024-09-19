#!/usr/bin/env python3
## author: l0g
## borrowed code from https://github.com/alphafox02/

import zmq
import json
import argparse
import signal
import sys
import datetime
from datetime import timezone
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

class Drone:
    """Represents a drone and its telemetry data."""
    def __init__(self, id: str, lat: float, lon: float, speed: float, vspeed: float, alt: float, height: float, pilot_lat: float, pilot_lon: float, description: str, start: any):
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
        self.start = start

    def update(self, lat: float, lon: float, speed: float, vspeed: float, alt: float, height: float, pilot_lat: float, pilot_lon: float, description: str, start: any):
        """Updates the drone's telemetry data and last seen time"""
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.vspeed = vspeed
        self.alt = alt
        self.height = height
        self.pilot_lat = pilot_lat
        self.pilot_lon = pilot_lon
        self.description = description
        self.start = start

    def to_dict(self) -> dict[str, any]:
        """Convert the Drone instance to a dictionary."""
        return {
            'id': self.id,
            #
            # Something weird is going on with the timestamps...
            # When the UTC timestamp is used, during the comparison for old drones in the 'update_or_add_drone' function, it adds an extra X hours to the unix epoch
            # I also included the original valued before my changes to the stale field just in case"""
            #
            #'time': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            #'start': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            #'stale': (datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            #'stale': (datetime.datetime.strptime(self.start,'%Y-%m-%dT%H:%M:%S.%fZ') + datetime.timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            #
            'time': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'start': self.start,
            'stale': (datetime.datetime.now() + datetime.timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'lat': self.lat,
            'lon': self.lon,
            'speed': self.speed,
            'vspeed': self.vspeed,
            'alt': self.alt,
            'height': self.height,
            'pilot_lat': self.pilot_lat,
            'pilot_lon': self.pilot_lon,
            'description': self.description
        }

def JSONWriter(file, data: list):
    """This sets up the JSON writer for writing drone data to file"""
    try:
        with open(file, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)
    except (IOError, TypeError) as e:
        print(f"An error occurred while writing to the file: {e}")

def parse_float(value: str) -> float:
    """Parses a string to a float, ignoring any extraneous characters."""
    try:
        return float(value.split()[0])
    except (ValueError, AttributeError):
        return 0.0

class DroneManager:
    """Manages a collection of drones and handles their updates."""    
    def __init__(self, max_drones=30, rate_limit=1.0):
        self.drones = deque(maxlen=max_drones)
        self.drone_dict = {}
        self.rate_limit = rate_limit
        self.last_sent_time = time.time()

    def update_or_add_drone(self, drone_id, drone_data):
        """Updates an existing drone or adds a new one to the collection."""
        if drone_id not in self.drone_dict:
            if len(self.drones) >= self.drones.maxlen:
                oldest_drone = self.drones.popleft()
                del self.drone_dict[oldest_drone.id]
            self.drones.append(drone_id)
            self.drone_dict[drone_id] = drone_data
        else:
            self.drone_dict[drone_id].update(
                lat=drone_data.lat,
                lon=drone_data.lon,
                speed=drone_data.speed,
                vspeed=drone_data.vspeed,
                alt=drone_data.alt,
                height=drone_data.height,
                pilot_lat=drone_data.pilot_lat,
                pilot_lon=drone_data.pilot_lon,
                description=drone_data.description,
                start=drone_data.start
            )
        
    def send_updates(self, file):
        """Sends updates to json file for reading from tarDRONE"""
        ## new updates
        data_to_write = []
        for drone_id in self.drones:
            # Current drone start time in Unix epoch
            x = int(datetime.datetime.strptime(self.drone_dict[drone_id].start,'%Y-%m-%dT%H:%M:%S.%fZ').timestamp())

            # Current time in Unix epoch
            y = int(datetime.datetime.now(datetime.UTC).timestamp())

            # Helpful in debugging timestamps and rate limit
            #print(f"Drone ID {self.drone_dict[drone_id].id} start epoch Time:\t {x}")
            #print(f"Drone ID {self.drone_dict[drone_id].id} current epoch Time:\t {y}")
            #print(f"Last Sent Time: {int(time.time() - self.last_sent_time)}")
            #print(f"Rate Limit Set: {self.rate_limit}")

            # Does rate limit need to apply here? ... not working yet
            #if ((y - x) < 5) and (time.time() - self.last_sent_time >= self.rate_limit):

            if ((y - x) < 5):
                data_to_write.append(self.drone_dict[drone_id].to_dict())
            try:
                JSONWriter(file, data_to_write)
                self.last_sent_time = time.time()
            except Exception as e:
                print(f"An error occurred while writing to the file: {e}")

def zmq_to_json(zmq_host, zmq_port, file, rate_limit: float = 1.0, max_drones: int = 30):
    context = zmq.Context()
    zmq_socket = context.socket(zmq.SUB)
    zmq_socket.connect(f"tcp://{zmq_host}:{zmq_port}")
    zmq_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    drone_manager = DroneManager(max_drones=max_drones, rate_limit=rate_limit)
    
    def signal_handler(sig, frame):
        print("Interrupted by user")
        zmq_socket.close()
        context.term()
        print("Cleaned up ZMQ resources")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            try:
                message = zmq_socket.recv_json()

                drone_info = {}
                for item in message:
                    if 'Basic ID' in item:
                        id_type = item['Basic ID'].get('id_type')
                        if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
                            drone_info['id'] = item['Basic ID'].get('id', 'unknown')
                            logger.debug(f"Parsed Serial Number ID: {drone_info['id']}")
                        elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
                            drone_info['id'] = item['Basic ID'].get('id', 'unknown')
                            logger.debug(f"Parsed CAA Assigned ID: {drone_info['id']}")
                    
                    if 'id' in drone_info:
                        if not drone_info['id'].startswith('drone-'):
                            drone_info['id'] = f"drone-{drone_info['id']}"
                            drone_info['start'] = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                            # This old value applies to the 'to_dict' function above for drone start times.
                            #drone_info['start'] = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        logger.debug(f"Ensured drone id with prefix: {drone_info['id']}")

                    # This would be used depending on what the incoming message is over Sniffle that provides the location information
                    if 'Location/Vector Message' in item:
                        drone_info['lat'] = parse_float(item['Location/Vector Message'].get('latitude', "0.0"))
                        drone_info['lon'] = parse_float(item['Location/Vector Message'].get('longitude', "0.0"))
                        drone_info['speed'] = parse_float(item['Location/Vector Message'].get('speed', "0.0"))
                        drone_info['vspeed'] = parse_float(item['Location/Vector Message'].get('vert_speed', "0.0"))
                        drone_info['alt'] = parse_float(item['Location/Vector Message'].get('geodetic_altitude', "0.0"))
                        drone_info['height'] = parse_float(item['Location/Vector Message'].get('height_agl', "0.0"))
                    if 'Location Vector' in item:
                        drone_info['lat'] = parse_float(item['Location Vector']['coord'].get('latitude', "0.0"))
                        drone_info['lon'] = parse_float(item['Location Vector']['coord'].get('longitude', "0.0"))
                        drone_info['speed'] = parse_float(item['Location Vector']['coord'].get('speed', "0.0"))
                        drone_info['vspeed'] = parse_float(item['Location Vector']['coord'].get('vert_speed', "0.0"))
                        drone_info['alt'] = parse_float(item['Location Vector']['coord'].get('geodetic_altitude', "0.0"))
                        drone_info['height'] = parse_float(item['Location Vector']['coord'].get('height_agl', "0.0"))

                    if 'Self-ID Message' in item:
                        drone_info['description'] = item['Self-ID Message'].get('text', "")

                    if 'System Message' in item:
                        drone_info['pilot_lat'] = parse_float(item['System Message'].get('latitude', "0.0"))
                        drone_info['pilot_lon'] = parse_float(item['System Message'].get('longitude', "0.0"))

                if 'id' in drone_info:
                    drone_id = drone_info['id']
                    drone = Drone(
                        id=drone_info['id'],
                        lat=drone_info.get('lat', 0.0),
                        lon=drone_info.get('lon', 0.0),
                        speed=drone_info.get('speed', 0.0),
                        vspeed=drone_info.get('vspeed', 0.0),
                        alt=drone_info.get('alt', 0.0),
                        height=drone_info.get('height', 0.0),
                        pilot_lat=drone_info.get('pilot_lat', 0.0),
                        pilot_lon=drone_info.get('pilot_lon', 0.0),
                        description=drone_info.get('description', ""),
                        start=drone_info.get('start')
                    )
                    drone_manager.update_or_add_drone(drone_id, drone)
                drone_manager.send_updates(file)
            
            except Exception as e:
                logger.error(f"Error receiving or processing message: {e}")

    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZMQ to TCP proxy for converting drone data to ANTSDR format.")
    parser = argparse.ArgumentParser(description="ZMQ to JSON for converting drone data to use with tarDRONE.")
    parser.add_argument("--zmq-host", default="127.0.0.1", help="ZMQ server host")
    parser.add_argument("--zmq-port", type=int, default=2402, help="ZMQ server port")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write parsed data to.")
    parser.add_argument("--max-drones", default=30, help="Number of drones to filter for")
    parser.add_argument("--rate-limit", default=1.0, help="Rate limit for sending updates to json file")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.info("Starting ZMQ to json with log level: %s","DEBUG" if args.debug else "INFO")

    zmq_to_json(args.zmq_host, args.zmq_port, '/run/readsb/drone.json')
