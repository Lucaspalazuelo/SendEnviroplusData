#!/usr/bin/env python3

import requests
import json
import time
import os
from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError, ChecksumMismatchError
from enviroplus import gas
from subprocess import PIPE, Popen, check_output
import pytz
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL")

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

import logging

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S'
)

handler = logging.FileHandler('error.log')
handler.setLevel(logging.ERROR)

formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)

logging.getLogger('').addHandler(handler)

paris_tz = pytz.timezone('Europe/Paris')
paris_time = datetime.now(paris_tz)

bus = SMBus(1)

# Create BME280 instance
bme280 = BME280(i2c_dev=bus)

# Create PMS5003 instance
pms5003 = PMS5003()

# Read values from BME280 and PMS5003 and return as dict
def read_enviro_values():
    values = {}
    cpu_temp = get_cpu_temperature()
    raw_temp = bme280.get_temperature()
    comp_temp = raw_temp - ((cpu_temp - raw_temp) / comp_factor)
    paris_time = datetime.now(paris_tz)
    values["date"] = paris_time.strftime("%d-%m-%Y %H:%M")
    values["temperature"] = "{:05.2f}".format(comp_temp)
    values["pressure"] = "{:05.2f}".format(bme280.get_pressure())
    values["humidity"] = "{:05.2f}".format(bme280.get_humidity())
    data = gas.read_all()
    values["oxidising"] = str(int(data.oxidising / 1000))
    values["reducing"] = str(int(data.reducing / 1000))
    values["nh3"] = str(int(data.nh3 / 1000))
    return values

def read_pm_values():
    values = {}
    paris_time = datetime.now(paris_tz)
    values["date"] = paris_time.strftime("%d-%m-%Y %H:%M")
    try:
        pm_values = pms5003.read()
        values["pm1"] = str(pm_values.pm_ug_per_m3(1.0))
        values["pm25"] = str(pm_values.pm_ug_per_m3(2.5))
        values["pm10"] = str(pm_values.pm_ug_per_m3(10))
    except(ReadTimeoutError, ChecksumMismatchError):
        logging.info("Failed to read PMS5003. Reseting and retrying.")
        pms5003.reset()
        pm_values = pms5003.read()
        values["pm1"] = str(pm_values.pm_ug_per_m3(1.0))
        values["pm25"] = str(pm_values.pm_ug_per_m3(2.5))
        values["pm10"] = str(pm_values.pm_ug_per_m3(10))
    return values

# Get the temperature of the CPU for compensation
def get_cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp

# Send data to API

def send_enviro_data(values):
    resp_enviro = None

    try:
        resp_enviro = requests.post(
            API_URL + "/enviro-data",
            data=json.dumps({
                "date": values['date'],
                "temperature": values['temperature'],
                "pressure": values['pressure'],
                "humidity": values['humidity'],
                "oxidising": values['oxidising'],
                "reducing": values['reducing'],
                "nh3": values['nh3']
            }),
            headers={
                "Content-Type": "application/json",
            },
            timeout=10
        )
    except requests.exceptions.ConnectionError as e:
        logging.warning('API Enviro Connection Error: {}'.format(e))
    except requests.exceptions.Timeout as e:
        logging.warning('API Enviro Timeout Error: {}'.format(e))
    except requests.exceptions.RequestException as e:
        logging.warning('API Enviro Request Error: {}'.format(e))

    if resp_enviro is not None:
        if resp_enviro.ok:
            return True
        else:
            logging.warning('API Enviro Error. Enviro: {}'.format(resp_enviro.reason))
            return False
    else:
        return False

def send_pm_data(values):
    resp_pm = None

    try:
        resp_pm = requests.post(
            API_URL + "/air-quality",
            data=json.dumps({
                "date": values['date'],
                "pm1": values['pm1'],
                "pm25": values['pm25'],
                "pm10": values['pm10']
            }),
            headers={
                "Content-Type": "application/json",
            },
            timeout=10
        )
    except requests.exceptions.ConnectionError as e:
        logging.warning('API PM Connection Error: {}'.format(e))
    except requests.exceptions.Timeout as e:
        logging.warning('API PM Timeout Error: {}'.format(e))
    except requests.exceptions.RequestException as e:
        logging.warning('API PM Request Error: {}'.format(e))

    if resp_pm is not None:
        if resp_pm.ok:
            return True
        else:
            logging.warning('Error. PM: {}'.format(resp_pm.reason))
            return False
    else:
        return False

# Compensation factor for temperature
comp_factor = 2.25

time_since_update = 0
update_time = time.time()
time_since_update2 = 0
update_time2 = time.time()

logging.info("Starting Enviro+ Data Send")

# Main loop to read data, display, and send to API
while True:
    try:
        enviro_values = read_enviro_values()
        pm_values = read_pm_values()
        time_since_update = time.time() - update_time
        time_since_update2 = time.time() - update_time2
        if time_since_update > 300:
            logging.info(pm_values)
            update_time = time.time()
            if send_pm_data(pm_values):
                logging.info("API pm Response: OK")
            else:
                logging.warning("API pm Response: Failed")
        if time_since_update2 > 900:
            logging.info(enviro_values)
            update_time2 = time.time()
            if send_enviro_data(enviro_values):
                logging.info("API enviro Response: OK")
            else:
                logging.warning("API enviro Response: Failed")
    except Exception as e:
        logging.warning('Main Loop Exception: {}'.format(e))
