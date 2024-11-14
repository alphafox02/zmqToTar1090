# Testing 

This folder provides some scripts so you can test the functionality without having a Sniffle dongle. It will simulate decoded drone packets coming out of the ZMQ decoder script.

## Start tar1090

- Using the README in the root folder of this repo, get tar1090 started with the correct configuration added to allow sniffing drones

## Simulate Drone Traffic

I like to do this with 3 terminal windows via tmux. Run the following commands (order does not matter)

```sh
python3 ./droneToZMQ.py
python3 ../zmqToTar1090.py
vim drone_lib.py
```

Once you run the above, after a few seconds you should see 3 drones come up in the tar1090 map.

While running, you can modify the `drone_lib.py` file to:
- change coordinates of spoofed drones
- change name of drone

This simulation only supports 3 drones, so you can't add additional values in the "id_num" nor the coordinate python lists. This is only a PoC to show that tar1090 is updating accordingly.

## How It Works

The `droneToZMQ.py` acts like the sniffle_receiver.py and zmq_decoder.py scripts from the Sniffle and DroneID projects.

It creates a ZMQ server, and will send JSON data read from `drone_send.json` out over ZMQ. This data is formatted in (hopefully) the same way that the Sniffle receiver would be reading it as if there was a drone flying around and you were sniffing its location data.

The zmqToTar1090.py will then read that data and send it to `/run/readsb/drone.json` for ingestion into tar1090.
