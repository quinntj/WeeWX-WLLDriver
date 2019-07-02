#
#    Copyright (c) 2019 Whoever wants to use it.
#    Tom Quinn is the original author, with bits taken from other drivers owned by other dudes. 
#

"""Weather Link Live Driver for the weewx weather system

To use this driver:

1) copy this file to the weewx user directory

   cp wlldriver.py /home/weewx/bin/user

2) configure weewx.conf

[Station]
    ...
    station_type = WeatherLinkLive
    ...
    [WeatherLinkLive]
    wllIP = <IP ADDRESS OF WEATHERLINK LIVE DEVICE>
    driver = user.wlldriver

"""

from __future__ import with_statement
import time
import socket
import os
import json
import struct
import syslog
import urllib2
from multiprocessing import Process
import requests
import weewx.drivers
import weeutil.weeutil

DRIVER_NAME = 'WeatherlinkLive'
DRIVER_VERSION = "1.0"

if weewx.__version__ < "3":
    raise weewx.UnsupportedFeature("weewx 3 is required, found %s" %
                                    weewx.__version__)

def logmsg(dst, msg):
    syslog.syslog(dst, 'wlinkLive: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logcrt(msg):
    logmsg(syslog.LOG_CRIT, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

def loader(config_dict, engine):
    return WLLDriver(**config_dict['WeatherLinkLive'])



class WLLDriver(weewx.drivers.AbstractDevice):
    """Weather Link Live"""

    def __init__(self, **stn_dict):
        self.wllIP = stn_dict['wllIP']
        self.stationData = None
        self.rain_previous_period = None
        
        if self.wllIP == None:
            logerr("WeatherLink Live IP address Not Entered")
            exit()
        else:
            loginf("WeatherLink Live IP: " + self.wllIP)

        self.url = "http://" + str(self.wllIP) + ":80/v1/current_conditions"
        loginf("version is %s" % DRIVER_VERSION)
        loginf("URL for data is %s" % self.url)


    def __enter__(self):
        self.open()
        return self

    def __exit__(self, _, value, traceback):
        self.close()


    def make_request_using_socket(self):
        logdbg("trying to get data...")
        resp = requests.get(self.url)
        logdbg("HTTP Response Code: "+ str(resp.status_code))
        json_data = json.loads(resp.text)
        if json_data["data"] == None:
            logerr (json_data["error"])
            logerr("HTTP Response Code: "+ str(resp.status_code))
            exit()
        else:
            self.stationData = json_data['data']['conditions']
            self.stationTimestampEpoch = json_data['data']['ts']
            logdbg(self.stationTimestampEpoch)
            logdbg(self.stationData)
            
            # Rain Calculations
            # rain collector type/size **(0: Reserved, 1: 0.01", 2: 0.2 mm, 3:  0.1 mm, 4: 0.001")**
            
            rainmultiplier = 0.01 #set default rain spoon capacity in case of issue with station info
            rain_this_period = 0 #set rain this period to 0 in case of issue with station info
            
            if self.stationData[0]["rain_size"] == 1:
                rainmultiplier = 0.01
            elif self.stationData[0]["rain_size"] == 2:
                rainmultiplier = 0.2
            elif self.stationData[0]["rain_size"] == 3:
                rainmultiplier = 0.1
            elif self.stationData[0]["rain_size"] == 4:
                rainmultiplier = 0.001

            
            rainrate = self.stationData[0]["rain_rate_last"]*rainmultiplier
            logdbg("Rain Rate: " + str(rainrate))
            
            #check to see if we're just starting so we can setup our rain calculations properly
            if self.rain_previous_period is not None:
               # do the calculation to compare rain now, with rain previous and use the difference in the loop packet
               rain_this_period = (self.stationData[0]["rainfall_daily"]-self.rain_previous_period)*rainmultiplier
               logdbg("Rain this period: " + str(rain_this_period))
            else:
               #not sure how else to handle this, if we don't have anything to compare to, we have to assume 0
               rain_this_period = self.stationData[0]["rainfall_daily"]
               logdbg("Fresh driver load rain set to station daily rain amount: "+ str(self.stationData[0]["rainfall_daily"]))

            #set the rain now so we can compare next loop
            self.rain_previous_period = self.stationData[0]["rainfall_daily"]
            logdbg("Set Previous period rain to: " + str(self.rain_previous_period))
            
            self.observations = {
                'dateTime' : self.stationTimestampEpoch,
                'outTemp' : self.stationData[0]["temp"],
                'heatindex' : self.stationData[0]["heat_index"],
                'windchill' : self.stationData[0]["wind_chill"],
                'inTemp' : self.stationData[1]["temp_in"],
                'barometer' : self.stationData[2]["bar_sea_level"],
                'pressure' : self.stationData[2]["bar_absolute"],
                'windSpeed' : self.stationData[0]["wind_speed_last"],
                'windDir' : self.stationData[0]["wind_dir_last"],
                'windGust' : self.stationData[0]["wind_speed_hi_last_10_min"],
                'windGustDir': self.stationData[0]["wind_dir_scalar_avg_last_10_min"],
                'outHumidity': self.stationData[0]["hum"],
                'inHumidity' : self.stationData[1]["hum_in"],
                'rain' : rain_this_period,
                'rainRate' : rainrate,
                'dewpoint' : self.stationData[0]["dew_point"],
                'windchill' : self.stationData[0]["wind_chill"],
                'txBatteryStatus' : self.stationData[0]["trans_battery_flag"]}
            logdbg(self.observations)

    def genLoopPackets(self):
        while True:
            self.real_time = True
        
            start_ts = self.the_time = time.time()
            self.loop_interval = 2.5

            # We are in real time mode. Try to keep synched up with the
            # wall clock
            sleep_time = self.the_time + self.loop_interval - time.time()
            if sleep_time > 0: 
                time.sleep(sleep_time)

            # Update the clock:
            self.the_time += self.loop_interval

            # Because a packet represents the measurements observed over the
            # time interval, we want the measurement values at the middle
            # of the interval.
            avg_time = self.the_time - self.loop_interval/2.0

            _packet = {'dateTime': int(self.the_time+0.5),
                        'usUnits' : weewx.US }

            self.make_request_using_socket()

            if hasattr(self, 'observations'):
                for k,v in self.observations.items():
                    _packet[k] = v
            yield _packet


    
@property
def hardware_name(self):
    return "WeatherlinkLive"

if __name__ == "__main__":
    usage = """%prog [options]"""
    import optparse
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--wllIP', dest='wllIP',
                        help='weatherlink live IP address')
    (options, args) = parser.parse_args()
    station = WLLDriver(wllIP=options.wllIP)
    for packet in station.genLoopPackets():
        print weeutil.weeutil.timestamp_to_string(packet['dateTime']), packet

