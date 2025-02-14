#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import datetime
import schedule
import time
from dateutil.relativedelta import relativedelta
from influxdb import InfluxDBClient
import gazpar
import json
import paho.mqtt.client as mqtt

import argparse
import logging
import pprint
from envparse import env

PFILE = "/.params"
DOCKER_MANDATORY_VARENV=['GRDF_USERNAME','GRDF_PASSWORD','GRDF_PCE','INFLUXDB_HOST','INFLUXDB_DATABASE','INFLUXDB_USERNAME','INFLUXDB_PASSWORD','MQTT_HOST']
DOCKER_OPTIONAL_VARENV=['INFLUXDB_PORT', 'INFLUXDB_SSL', 'INFLUXDB_VERIFY_SSL','MQTT_PORT','MQTT_KEEPALIVE','MQTT_TOPIC']


# Sub to return format wanted by linky.py
def _dayToStr(date):
    return date.strftime("%d/%m/%Y")

# Open file with params for influxdb, GRDF API
def _openParams(pfile):
    # Try to load environment variables
    if set(DOCKER_MANDATORY_VARENV).issubset(set(os.environ)):
        return {'grdf': {'username': env(DOCKER_MANDATORY_VARENV[0]),
                         'password': env(DOCKER_MANDATORY_VARENV[1]),
                         'pce': env(DOCKER_MANDATORY_VARENV[2])},
                'influx': {'host': env(DOCKER_MANDATORY_VARENV[3]),
                           'port': env.int(DOCKER_OPTIONAL_VARENV[0], default=8086),
                           'db': env(DOCKER_MANDATORY_VARENV[4]),
                           'username': env(DOCKER_MANDATORY_VARENV[5]),
                           'password': env(DOCKER_MANDATORY_VARENV[6]),
                           'ssl': env.bool(DOCKER_OPTIONAL_VARENV[1], default=True),
                           'verify_ssl': env.bool(DOCKER_OPTIONAL_VARENV[2], default=True)},
                'mqtt': {'host': env(DOCKER_MANDATORY_VARENV[7]),
                         'port': env.int(DOCKER_OPTIONAL_VARENV[3], default=1883),
                         'keepalive': env.int(DOCKER_OPTIONAL_VARENV[4], default=60),
                         'topic': env(DOCKER_OPTIONAL_VARENV[5], default='gazpar/')}}
    # Try to load .params then programs_dir/.params
    elif os.path.isfile(os.getcwd() + pfile):
        p = os.getcwd() + pfile
    elif os.path.isfile(os.path.dirname(os.path.realpath(__file__)) + pfile):
        p = os.path.dirname(os.path.realpath(__file__)) + pfile
    else:
        if (os.getcwd() + pfile != os.path.dirname(os.path.realpath(__file__)) + pfile):
            logging.error('file %s or %s not exist', os.path.realpath(os.getcwd() + pfile) , os.path.dirname(os.path.realpath(__file__)) + pfile)
        else:
            logging.error('file %s not exist', os.getcwd() + pfile )
        sys.exit(1)
    try:
        f = open(p, 'r')
        try:
            array = json.load(f)
        except ValueError as e:
            logging.error('decoding JSON has failed', e)
            sys.exit(1)
    except IOError:
        logging.error('cannot open %s', p)
        sys.exit(1)
    else:
        f.close()
        return array


# Sub to get StartDate depending today - daysNumber
def _getStartDate(today, daysNumber):
    return _dayToStr(today - relativedelta(days=daysNumber))

# Get the midnight timestamp for startDate
def _getStartTS(daysNumber):
    date = (datetime.datetime.now().replace(hour=12,minute=0,second=0,microsecond=0) - relativedelta(days=daysNumber))
    return date.timestamp()

# Get the timestamp for calculating if we are in HP / HC
def _getDateTS(y,mo,d,h,m):
    date = (datetime.datetime(year=y,month=mo,day=d,hour=h,minute=m,second=0,microsecond=0))
    return date.timestamp()

# Get startDate with influxDB lastdate +1
def _getStartDateInfluxDb(client,measurement):
    result = client.query("SELECT * from " + measurement + " ORDER BY time DESC LIMIT 1")
    data = list(result.get_points())
    datar = data[0]['time'].split('T')
    datarr = datar[0].split('-')
    return datarr

def _createDataToPublish(resGrdf, startTimestamp, endDate):
    # When we have all values let's start parse data and pushing it to InfluxDB
    jsonData = []
    for d in resGrdf:
        t = datetime.datetime.strptime(d['journeeGaziere'] + " 12:00", '%Y-%m-%d %H:%M')
        logging.info(("found value : {0:3} kWh / {1:7.2f} m3 at {2}").format(d['energieConsomme'], d['volumeBrutConsomme'], t.strftime('%Y-%m-%dT%H:%M:%SZ')))
        if t.timestamp() > startTimestamp:
            logging.info(("value added to jsonData as {0} > {1}").format(t.strftime('%Y-%m-%d %H:%M'), datetime.datetime.fromtimestamp(startTimestamp).strftime('%Y-%m-%d %H:%M')))
            jsonData.append({
                           "measurement": "Gazpar",
                           "time": t.strftime('%Y-%m-%dT%H:%M:%SZ'),
                           "fields": {
                               "value": d['energieConsomme'],
                               "kWh": d['energieConsomme'],
                               "mcube": d['volumeBrutConsomme']
                           }
                         })
        else:
            logging.info(("value NOT added to jsonData as {0} > {1}").format(t.timestamp(), startTimestamp))
    return jsonData
        
# Let's start here !
def main():
    clientInflux = None
    clientMqtt = None
    params = _openParams(PFILE)

    # Try to log in InfluxDB Server
    try:
        logging.info("logging in InfluxDB Server Host %s...", params['influx']['host'])
        clientInflux = InfluxDBClient(params['influx']['host'], params['influx']['port'],
                    params['influx']['username'], params['influx']['password'],
                    params['influx']['db'], ssl=params['influx']['ssl'], verify_ssl=params['influx']['verify_ssl'])
        logging.info("logged in InfluxDB Server Host %s succesfully", params['influx']['host'])
    except:
        logging.error("unable to login on %s", params['influx']['host'])
        sys.exit(1)

    # Try to log in MQTT
    try:
        logging.info("logging in MQTT %s...", params['mqtt']['host'])
        clientMqtt = mqtt.Client()
        clientMqtt.connect(params['mqtt']['host'], params['mqtt']['port'], params['mqtt']['keepalive'])
    except:
        logging.error("unable to connect to %s", params['mqtt']['host'])
   
    # Calculate start/endDate and firstTS for data to request/parse
    if args.last:
        logging.info("looking for last value date on InfluxDB 'conzo_gaz' on host %s...", params['influx']['host'])
        startDate = _getStartDateInfluxDb(clientInflux, "Gazpar")
        logging.info("found last fetch date %s on InfluxDB 'Gazpar' on host %s...", startDate[2]+"/"+startDate[1]+"/"+startDate[0], params['influx']['host'])
        firstTS =  _getDateTS(int(startDate[0]),int(startDate[1]),int(startDate[2]),12,0)
        startDate = startDate[2]+"/"+startDate[1]+"/"+startDate[0]
    else :
        logging.warning("GRDF may not all the data for the last %s days ", args.days)
        startDate = _getStartDate(datetime.date.today(), args.days)
        firstTS =  _getStartTS(args.days)

    logging.info("will use %s as firstDate and %s as startDate", firstTS, startDate)
    endDate = _dayToStr(datetime.date.today())

    try:
        grdf_client = gazpar.Gazpar(params['grdf']['username'], params['grdf']['password'], params['grdf']['pce'])
        resGrdf = grdf_client.get_consumption()
    except Exception as exc:
        strErrMsg = "[Error] " + str(exc)
        logging.error(strErrMsg)
        print(strErrMsg)
        return False
    if (args.verbose):
        pp.pprint(resGrdf)

    jsonData = _createDataToPublish(resGrdf['releves'], firstTS, endDate)
    if (args.verbose):
        pp.pprint(jsonData)
    logging.info("trying to write {0} points to influxDB".format(len(jsonData)))

    try:
        clientInflux.write_points(jsonData)
    except:
        logging.info("unable to write data points to influxdb")
    else:
        logging.info("done")

    if clientMqtt:
        logging.info("trying to write {0} points to MQTT".format(len(jsonData)))
        try:
            for data in jsonData:
                clientMqtt.publish(params['mqtt']['topic'] + 'json', json.dumps(data), 0)
        except:
            logging.info("unable to write data to mqtt")
        else:
            logging.info("done")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--days", type=int,
                        help="Number of days from now to download", default=1)
    parser.add_argument("-l", "--last", action="store_true",
                        help="Check from InfluxDb the number of missing days", default=False)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="More verbose", default=False)
    parser.add_argument("-s", "--schedule", 
                        help="Schedule the launch of the script at hh:mm everyday")
    args = parser.parse_args()

    pp = pprint.PrettyPrinter(indent=4)
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

    if args.schedule:
        logging.info(args.schedule)
        schedule.every().day.at(args.schedule).do(main)
        while True:
            schedule.run_pending()
            time.sleep(1)
    else:
        main()
