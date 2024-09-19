# Testing 

This folder provides some scripts so you can test the functionality without having a Sniffle dongle.

## Start tar1090

- Using the README in the root folder of this repo, get tar1090 started with the correct configuration added to allow sniffing drones

## Begin spoofing drones

I like to do this with 3 terminal windows via tmux. Run the following commands (order doesn't matter)

```sh
python3 ./droneToZMQ.py
python3 ../zmqToTar1090.py
vim drone_lib.py
```

Once you run the above, you should start to see 3 drones come up in the tar1090 map

You can on-the-fly modify the `drone_lib.py` file to:
- change coordinates of spoofed drones
- change name of drone

This only supports 3 drones, so you can't add additional values in the "id_num" nor the coordinate python lists. This is only a POC to show that tar1090 is updating accordingly.

## How It Works

The `droneToZMQ.py` acts like the sniffle_receiver.py script from the Sniffle project.

It creates a ZMQ server, and will send JSON data read from `drone_send.json` out over ZMQ. This data is formatted in (hopefully) the same way that the Sniffle receiver would be reading it as if there was a drone flying around and you were sniffing it's location data.

The zmqToTar1090.py will then read that data and send it to `/run/readsb/drone.json` for ingestion into tar1090.
