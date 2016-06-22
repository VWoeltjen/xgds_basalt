#__BEGIN_LICENSE__
# Copyright (c) 2015, United States Government, as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All rights reserved.
#
# The xGDS platform is licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0.
#
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
#__END_LICENSE__

from geocamUtil import KmlUtil

from xgds_planner2.planExporter import TreeWalkPlanExporter
from xgds_planner2 import xpjson
from polycircles import polycircles


class KmlPlanExporter(TreeWalkPlanExporter):
    """
    Exports plan as KML string.
    """

    label = 'kml'
    content_type = 'application/vnd.google-earth.kml+xml'

    def transformStation(self, station, tsequence, context):
        lon, lat = station.geometry['coordinates']
        name = station.name
        if not name:
            # use the number from the id
            sindex = station.id.find('STN')
            if sindex >=0:
                name = station.id[sindex+3:]
            else:
                name = station.id
        name = "__" + name
        directionStyle = None
        styleUrl = 'station'
        result = ""
        try:
            if station.isDirectional:
                if station.headingDegrees:
                    headingDegrees = float(station.headingDegrees)
                    styleUrl = 'heading'
                    directionStyle = KmlUtil.makeStyle(iconHeading=headingDegrees)
        except AttributeError:
            pass
        result = result + ('''
<Placemark>
  <name>%s</name>
  <styleUrl>%s</styleUrl>''' % (name, styleUrl))
        if directionStyle:
            result = result + directionStyle
        result = result + ('''
  <Point>
    <coordinates>%(lon)s,%(lat)s</coordinates>
  </Point>
</Placemark>''' % {'lon': lon, 'lat': lat})
        
        if station.boundary:
            boundaryCircle = polycircles.Polycircle(latitude=lat,
                                                    longitude=lon,
                                                    radius=station.boundary,
                                                    number_of_vertices=36)
            result += '''
<Placemark>
  <name>%(name)s</name>
  <styleUrl>#boundary</styleUrl>
  <MultiGeometry>
    <LineString>
      <tessellate>1</tessellate>
      <coordinates>
''' % {'name': name + ' boundary'}
        for coord in boundaryCircle.vertices:
            result = result + str(coord[1]) + ',' + str(coord[0]) + '\n'
        result = result + str(boundaryCircle.vertices[0][1]) + ',' + str(boundaryCircle.vertices[0][0]) + '\n'
        result = result + '''
      </coordinates>
    </LineString>
  </MultiGeometry>
</Placemark>
'''
        if station.tolerance:
            toleranceCircle = polycircles.Polycircle(latitude=lat,
                                                     longitude=lon,
                                                     radius=station.tolerance,
                                                     number_of_vertices=36)
            result += '''
<Placemark>
  <name>%(name)s</name>
  <styleUrl>#tolerance</styleUrl>
    <LineString>
      <tessellate>1</tessellate>
      <coordinates>
''' % {'name': name + ' tolerance'}
        for coord in toleranceCircle.vertices:
            result = result + str(coord[1]) + ',' + str(coord[0]) + '\n'
        result = result + str(toleranceCircle.vertices[0][1]) + ',' + str(toleranceCircle.vertices[0][0]) + '\n'
        result = result + '''
      </coordinates>
    </LineString>
</Placemark>
'''
        return result

    def transformSegment(self, segment, tsequence, context):
        coords = [context.prevStation.geometry['coordinates']]
        if segment.geometry and segment.geometry['coordinates']:
            coords = coords[:0]
            coords.extend(segment.geometry['coordinates'])
        coords.append(context.nextStation.geometry['coordinates'])

        result = '''
<Placemark>
  <name>%(name)s</name>
  <styleUrl>segment</styleUrl>
  <MultiGeometry>
    <LineString>
      <tessellate>1</tessellate>
      <coordinates>
''' % {'name': segment.id }
        for coord in coords:
            result = result + str(coord[0]) + ',' + str(coord[1]) + '\n'
        result = result + '''
      </coordinates>
    </LineString>
  </MultiGeometry>
</Placemark>
'''
        return result

    def makeStyles(self):
        waypointStyle = KmlUtil.makeStyle("station", "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png", 0.85)
        directionStyle = KmlUtil.makeStyle("heading", iconUrl="http://earth.google.com/images/kml-icons/track-directional/track-0.png")
        segmentStyle = KmlUtil.makeStyle("segment", lineWidth=3, lineColor="FF00FFFF")
        boundaryStyle = KmlUtil.makeStyle("boundary", lineWidth=3, lineColor="FF0099FF")
        toleranceStyle = KmlUtil.makeStyle("tolerance", lineWidth=3, lineColor="FF00FFFF", )
        return waypointStyle + directionStyle + segmentStyle + boundaryStyle + toleranceStyle

    def transformPlan(self, plan, tsequence, context):
        name = plan.get("name")
        if not name:
            name = plan.get("id", "")
        return KmlUtil.wrapKmlDocument(self.makeStyles() + '\n'.join(tsequence), name)


def test():
    schema = xpjson.loadDocument(xpjson.EXAMPLE_PLAN_SCHEMA_PATH)
    plan = xpjson.loadDocument(xpjson.EXAMPLE_PLAN_PATH, schema=schema)
    exporter = KmlPlanExporter()
    open('/tmp/foo.kml', 'wb').write(exporter.exportPlan(plan, schema))


if __name__ == '__main__':
    test()
