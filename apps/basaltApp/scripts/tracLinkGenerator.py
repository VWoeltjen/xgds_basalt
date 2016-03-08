#!/usr/bin/env python
# __BEGIN_LICENSE__
#Copyright (c) 2015, United States Government, as represented by the 
#Administrator of the National Aeronautics and Space Administration. 
#All rights reserved.
#
#The xGDS platform is licensed under the Apache License, Version 2.0 
#(the "License"); you may not use this file except in compliance with the License. 
#You may obtain a copy of the License at 
#http://www.apache.org/licenses/LICENSE-2.0.
#
#Unless required by applicable law or agreed to in writing, software distributed 
#under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR 
#CONDITIONS OF ANY KIND, either express or implied. See the License for the 
#specific language governing permissions and limitations under the License.
# __END_LICENSE__

"""
A server that simulates the TracLink server. The server listens
on a TCP port, and clients can connect to receive position data.

By default, the server just replays data from a log of real TracLink
output from a test at KSC. But you can overwrite fields in the records
for your testing convenience using the options.
"""

import socket
import time
import datetime
import pytz
import logging
import os
import math
import calendar

# pylint: disable=E1101

EARTH_RADIUS_METERS = 6371010

# When the --fakePos option is specified, the target and the chase
# boat drive in a circle around FAKE_POS_CENTER.

if 1:
    # Pavilion Lake
    FAKE_POS_CENTER_LATITUDE = 51.008101
    FAKE_POS_CENTER_LONGITUDE = -121.776976
    FAKE_POS_RADIUS_METERS = 10000
    FAKE_POS_VELOCITY_METERS_PER_SECOND = 500

if 0:
    # Lake Denton, Central Florida
    FAKE_POS_CENTER_LATITUDE = 27.5562625
    FAKE_POS_CENTER_LONGITUDE = -81.4893358
    FAKE_POS_RADIUS_METERS = 150
    FAKE_POS_VELOCITY_METERS_PER_SECOND = 10

if 0:
    # Nanaimo
    FAKE_POS_CENTER_LATITUDE = 49.207917
    FAKE_POS_CENTER_LONGITUDE = -123.962326
    FAKE_POS_RADIUS_METERS = 100
    FAKE_POS_VELOCITY_METERS_PER_SECOND = 10

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def getLonLat(centerLon, centerLat, dx, dy):
    degrees_per_meter_lat = 1.0 / EARTH_RADIUS_METERS * (180. / math.pi)
    degrees_per_meter_lon = degrees_per_meter_lat / math.cos(centerLat * math.pi / 180.)
    return (centerLon + degrees_per_meter_lon * dx,
            centerLat + degrees_per_meter_lat * dy)


def getFakePosition(dt):
    timeSeconds = calendar.timegm(dt.timetuple())
    angularVelocity = float(FAKE_POS_VELOCITY_METERS_PER_SECOND) / FAKE_POS_RADIUS_METERS
    phase = (timeSeconds * angularVelocity) % (2 * math.pi)
    dx = FAKE_POS_RADIUS_METERS * math.cos(phase)
    dy = FAKE_POS_RADIUS_METERS * math.sin(phase)
    return getLonLat(FAKE_POS_CENTER_LONGITUDE,
                     FAKE_POS_CENTER_LATITUDE,
                     dx, dy)


def formatDM(val):
    sign = -1 if val < 0 else 1
    signStr = '-' if val < 0 else ''
    val = val / sign
    degrees = int(val)
    val = val - degrees
    minutes = val * 60
    return '%s%d%07.4f' % (signStr, degrees, minutes)


def tracLinkGenerator(opts):
    f = open(os.path.join(THIS_DIR, 'SampleTracLink20130728.txt'),
             "r")
    sampleTrackingData = f.read()

    host = '127.0.0.1'
    port = 50000
    backlog = 5
    print 'listening on %s:%s' % (host, port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # http://stackoverflow.com/questions/6380057/python-binding-socket-address-already-in-use
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    s.bind((host, port))
    s.listen(backlog)

    while True:
        client, _address = s.accept()
        logging.info('connection established')
        try:
            boatTimeOffset = datetime.timedelta(seconds=10)
            while True:
                for line in sampleTrackingData.split("\n"):
                    if len(line) != 0:
                        targetId, d, t, lat, lon, shipLat, shipLon, shipHeading, depth = line.split(',')
                        now = datetime.datetime.now(pytz.utc)
                        if opts.age:
                            now -= datetime.timedelta(seconds=opts.age)
                        if 1:
                            # overwrite timestamp with current UTC time for testing live view
                            d = now.strftime("%m/%d/%y")
                            t = now.strftime("%H:%M:%S")
                        if opts.fakePositions:
                            # overwrite position
                            lon, lat = getFakePosition(now)
                            lon = formatDM(lon)
                            lat = formatDM(lat)
                            shipLon, shipLat = getFakePosition(now - boatTimeOffset)
                            shipLon = formatDM(shipLon)
                            shipLat = formatDM(shipLat)
                        if opts.beaconId != -1:
                            targetId = str(opts.beaconId)
                        newLine = ",".join((targetId,
                                            d, t,
                                            lat, lon,
                                            shipLat, shipLon, shipHeading,
                                            depth))
                        logging.info(newLine)
                        client.send(newLine + "\n")
                        time.sleep(opts.period)
        except socket.error:
            logging.warning('socket error. broken pipe? will try to accept another connection')


def main():
    import optparse
    parser = optparse.OptionParser('usage: %prog\n' + __doc__)
    parser.add_option('-a', '--age',
                      type='float', default=0.0,
                      help='Age of produced timestamps in seconds [%default]')
    parser.add_option('-p', '--period',
                      type='float', default=5.0,
                      help='Period between messages in seconds [%default]')
    parser.add_option('-f', '--fakePositions',
                      action='store_true', default=False,
                      help='Output fake positions instead of logged positions')
    parser.add_option('-b', '--beaconId',
                      type='int', default=-1,
                      help='Output specified beacon id instead of logged id')
    opts, args = parser.parse_args()
    if args:
        parser.error('expected no args')
    logging.basicConfig(level=logging.DEBUG)

    tracLinkGenerator(opts)


if __name__ == '__main__':
    main()