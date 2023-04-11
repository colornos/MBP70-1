from __future__ import print_function
import sys
import pygatt.backends
import logging
from configparser import ConfigParser
import time
import subprocess
from struct import *
from binascii import hexlify
import os
import threading
import requests

# Interesting characteristics
Char_temperature = '00002A1C-0000-1000-8000-00805f9b34fb'  # temperature data


def sanitize_timestamp(timestamp):
    retTS = time.time()
    return retTS


def decodetemperature(handle, values):
    data = unpack('<BHxxxxxxI', bytes(values[0:14]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x02)
    retDict["temperature"] = data[1]
    retDict["timestamp"] = sanitize_timestamp(data[2])
    return retDict


def processIndication(handle, values):
    if handle == handle_temperature:
        result = decodetemperature(handle, values)
        if result not in temperaturedata:
            log.info(str(result))
            temperaturedata.append(result)
        else:
            log.info('Duplicate temperaturedata record')
    else:
        log.debug('Unhandled Indication encountered')


def wait_for_device(devname):
    found = False
    while not found:
        try:
            found = adapter.filtered_scan(devname)
        except pygatt.exceptions.BLEError:
            adapter.reset()
    return


def connect_device(address):
    device_connected = False
    tries = 3
    device = None
    while not device_connected and tries > 0:
        try:
            device = adapter.connect(address, 8, addresstype)
            device_connected = True
        except pygatt.exceptions.NotConnectedError:
            tries -= 1
    return device


def init_ble_mode():
    p = subprocess.Popen("sudo btmgmt le on", stdout=subprocess.PIPE, shell=True)
    (output, err) = p.communicate()
    if not err:
        log.info(output)
        return True
    else:
        log.info(err)
        return False


config = ConfigParser()
config.read('MBP70.ini')
path = "plugins/"
plugins = {}

numeric_level = getattr(logging,
                        config.get('Program', 'loglevel').upper(),
                        None)
if not isinstance(numeric_level, int):
    raise ValueError('Invalid log level: %s' % loglevel)
logging.basicConfig(level=numeric_level,
                    format='%(asctime)s %(levelname)-8s %(funcName)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=config.get('Program', 'logfile'),
                    filemode='w')
log = logging.getLogger(__name__)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(numeric_level)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(funcName)s %(message)s')
ch.setFormatter(formatter)
log.addHandler(ch)

if config.has_option('Program', 'plugins'):
    config_plugins = config.get('Program', 'plugins').split(',')
    config_plugins = [plugin.strip(' ') for plugin in config_plugins]
    log.info('Configured plugins: %s' % ', '.join(config_plugins))

    sys.path.insert(0, path)
    for plugin in config_plugins:
        log.info('Loading plugin: %s' % plugin)
        mod = __import__(plugin)
        plugins[plugin] = mod.Plugin()
    log.info('All plugins loaded.')
else:
    log.info('No plugins configured.')
sys.path.pop(0)

ble_address = config.get('TEMP', 'ble_address')
device_name = config.get('TEMP', 'device_name')
device_model = config.get('TEMP', 'device_model')

if device_model == 'MBP70':
    addresstype = pygatt.BLEAddressType.public
    time_offset = 0
else:
    addresstype = pygatt.BLEAddressType.random
    time_offset = 0

log.info('MBP70 Started')
if not init_ble_mode():
    sys.exit()

adapter = pygatt.backends.GATTToolBackend()
adapter.start()

while True:
    wait_for_device(device_name)
    device = connect_device(ble_address)
    if device:
        temperaturedata = []
        handle_temperature = device.get_handle(Char_temperature)
        continue_comms = True

        try:
            device.subscribe(Char_temperature,
                             callback=processIndication,
                             indication=True)
        except pygatt.exceptions.NotConnectedError:
            continue_comms = False

        if continue_comms:
            log.info('Waiting for notifications for another 30 seconds')
            time.sleep(30)
            try:
                device.disconnect()
            except pygatt.exceptions.NotConnectedError:
                log.info('Could not disconnect...')

            log.info('Done receiving data from temperature thermometer')

            if temperaturedata:
                temperaturedatasorted = sorted(temperaturedata, key=lambda k: k['timestamp'], reverse=True)

                for plugin in plugins.values():
                    plugin.execute(config, temperaturedatasorted)
            else:
                log.error('Data received')