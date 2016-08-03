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
import time
import pytz
import traceback
import os
import datetime
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.core.cache import cache 

from taggit.managers import TaggableManager

from geocamTrack import models as geocamTrackModels
from geocamTrack.utils import getClosestPosition

from geocamUtil.models.AbstractEnum import AbstractEnumModel
from xgds_planner2 import models as plannerModels
from xgds_sample import models as xgds_sample_models
from geocamUtil.loader import LazyGetModelByName
from xgds_core.models import Constant
from xgds_notes2.models import AbstractLocatedNote, AbstractUserSession, AbstractTaggedNote, Location, NoteMixin, NoteLinksMixin

from xgds_image import models as xgds_image_models
from xgds_planner2.utils import getFlight
from xgds_planner2.models import AbstractActiveFlight
from xgds_planner2.views import getActiveFlights
from xgds_instrument.models import ScienceInstrument, AbstractInstrumentDataProduct

from geocamPycroraptor2.views import getPyraptordClient, stopPyraptordServiceIfRunning
from xgds_data.introspection import verbose_name

from xgds_video.models import *
from xgds_video.recordingUtil import getRecordedVideoDir, getRecordedVideoUrl, startRecording, stopRecording
from xgds_video.recordingUtil import endActiveEpisode, startFlightRecording, stopFlightRecording

from xgds_status_board.models import *

from subprocess import Popen
import re

from django.core.cache import caches  
_cache = caches['default']

LOCATION_MODEL = LazyGetModelByName(settings.GEOCAM_TRACK_PAST_POSITION_MODEL)
VIDEO_SOURCE_MODEL = LazyGetModelByName(settings.XGDS_VIDEO_SOURCE_MODEL)
VIDEO_EPISODE_MODEL = LazyGetModelByName(settings.XGDS_VIDEO_EPISODE_MODEL)

class BasaltResource(geocamTrackModels.AbstractResource):
    resourceId = models.IntegerField(null=True, blank=True, db_index=True) # analogous to beacon id, identifier for track inputs
    vehicle = models.OneToOneField(plannerModels.Vehicle, blank=True, null=True)
    port = models.IntegerField(null=True, blank=True)

    def __unicode__(self):
        return self.name
    

class DataType(models.Model):
    name = models.CharField(max_length=32)
    notes = models.TextField(null=True, blank=True)

    def __unicode__(self):
        return self.name


class BasaltTrack(geocamTrackModels.AbstractTrack):
    # set foreign key fields required by parent model to correct types for this site
    resource = models.ForeignKey(BasaltResource,
                                 related_name='%(app_label)s_%(class)s_related',
                                 verbose_name='Asset', blank=True, null=True)
    iconStyle = geocamTrackModels.DEFAULT_ICON_STYLE_FIELD()
    lineStyle = geocamTrackModels.DEFAULT_LINE_STYLE_FIELD()

    dataType = models.ForeignKey(DataType, null=True, blank=True)
    timezone = models.CharField(max_length=128, default=settings.TIME_ZONE, db_index=True)

    @classmethod
    def getTrackByName(cls, trackName):
        try:
            track = cls.objects.get(name=trackName)
        except cls.DoesNotExist:
            track = None
        return track

    def getLabelName(self, pos):  # Returned shortened name for display
        return "__%s" % self.resource.vehicle.name

    def getTimezone(self):
        return pytz.timezone(self.timezone)
    
#     def toMapDict(self):
#         result = geocamTrackModels.AbstractTrack.toMapDict(self)
#         if result:
#             result['type'] = settings.GEOCAM_TRACK_TRACK_MONIKIER
#         return result
    
    def __unicode__(self):
        return '%s %s' % (self.__class__.__name__, self.name)


class AbstractBasaltPosition(geocamTrackModels.AltitudeResourcePositionNoUuid):
    # set foreign key fields required by parent model to correct types for this site
    track = models.ForeignKey(BasaltTrack, db_index=True, null=True, blank=True)

    serverTimestamp = models.DateTimeField(db_index=True)

    class Meta:
        abstract = True


class CurrentPosition(AbstractBasaltPosition):
    pass


class PastPosition(AbstractBasaltPosition):
    pass


class EV(models.Model):
    '''
    An EV is a user who can execute a plan.  Information must be provided to Pextant
    about the user to correctly model the path
    ''' 
    mass = models.FloatField(db_index=True)
    user = models.OneToOneField(User)
    
    def toSimpleDict(self):
        result = {}
        result['mass'] = self.mass
        result['name'] = self.user.first_name + ' ' + self.user.last_name
        result['pk'] = self.pk
        return result
    
    def __unicode__(self):
        return self.user.first_name + ' ' + self.user.last_name

class BasaltGroupFlight(plannerModels.AbstractGroupFlight):
    
    @property
    def videoEpisode(self):
        # because we do not replicate the video episode table we look it up instead of having a foreign key
        try:
            foundEpisode = VIDEO_EPISODE_MODEL.get().objects.get(shortName=self.name)
            return foundEpisode
        except:
#             traceback.print_exc()
            return None

    @property
    def view_url(self):
        return reverse('xgds_video_recorded', kwargs={'flightName':self.name})
    
    @property
    def flights(self):
        return self.basaltflight_set.all()



class BasaltFlight(plannerModels.AbstractFlight):
    ''' A Basalt Flight for storing delay and handling start and stop functions '''
    # set foreign key fields required by parent model to correct types for this site
    vehicle = plannerModels.DEFAULT_VEHICLE_FIELD()
    group = models.ForeignKey(BasaltGroupFlight, null=True, blank=True)

    delaySeconds = models.IntegerField(default=0)
    track = models.OneToOneField(BasaltTrack, null=True, blank=True)
    
    videoSource = models.ForeignKey(settings.XGDS_VIDEO_SOURCE_MODEL, null=True, blank=True)

    def thumbnail_time_url(self, event_time):
        return reverse('videoStillThumb', kwargs={'flightName':self.name, 'time':event_time})

    def thumbnail_url(self):
        return reverse('videoStillThumb', kwargs={'flightName':self.name, 'time':0})

    def view_time_url(self, event_time):
        sourceShortName = self.name.split('_')[1]
        return reverse('xgds_video_recorded_time', kwargs={'flightName':self.name, 
                                                           'sourceShortName':sourceShortName,
                                                           'time':event_time})
    
    def view_url(self):
        return reverse('xgds_video_recorded', kwargs={'flightName':self.name})
    
    def getVideoSource(self):
        if self.videoSource:
            return self.videoSource
        self.videoSource = VIDEO_SOURCE_MODEL.get().objects.get(shortName=self.vehicle.name)
        self.save()
        return self.videoSource

    def getResource(self):
        resource=LazyGetModelByName(settings.GEOCAM_TRACK_RESOURCE_MODEL).get().objects.get(vehicle=self.vehicle)
        return resource 

    def startTracking(self):
        resource=self.getResource()
        
        #Create the track if it does not exist
        if not self.track:
            TRACK_MODEL = LazyGetModelByName(settings.GEOCAM_TRACK_TRACK_MODEL)
            try:
                track = TRACK_MODEL.get().objects.get(name=self.name)
            except ObjectDoesNotExist:
                timezone = settings.TIME_ZONE
                if self.plans:
                    timezone=str(self.plans[0].plan.jsonPlan.site.alternateCrs.properties.timezone)
                    self.timezone = timezone
                track = TRACK_MODEL.get()(name=self.name,
                                          resource=resource,
                                          timezone=timezone,
                                          iconStyle=geocamTrackModels.IconStyle.objects.get(uuid=resource.name),
                                          lineStyle=geocamTrackModels.LineStyle.objects.get(uuid=resource.name),
                                          dataType=DataType.objects.get(name="RawGPSLocation"))
                track.save()
                self.track = track
                
                # this is for archival purposes; make sure remoteDelay is set for the other server's delay.
                delayConstant = Constant.objects.get(name="remoteDelay")
                self.delaySeconds = int(delayConstant.value)
                self.save()
    
        if settings.PYRAPTORD_SERVICE is True:
            pyraptord = getPyraptordClient()
            serviceName = self.vehicle.name + "TrackListener"
            ipAddress = Constant.objects.get(name=resource.name + "_TRACKING_IP")
            scriptPath = os.path.join(settings.PROJ_ROOT, 'apps', 'basaltApp', 'scripts', 'evaTrackListener.py')
            command = "%s -o %s -p %d -n %s -t %s" % (scriptPath, ipAddress.value, resource.port, self.vehicle.name[-1:], self.name)
            stopPyraptordServiceIfRunning(pyraptord, serviceName)
            time.sleep(2)
            pyraptord.updateServiceConfig(serviceName,
                                          {'command': command})
            pyraptord.startService(serviceName)
        
#     def startVideoRecording(self):
#         flightGroup = self.group
#         if not flightGroup.videoEpisode:
#             videoEpisode = VideoEpisode(shortName=flightGroup.name, startTime=self.start_time )
#             videoEpisode.save()
#         else:
#             if flightGroup.videoEpisode.endTime:
#                 flightGroup.videoEpisode.endTime = None
# 
#         recordingDir = getRecordedVideoDir(self.name)
#         recordingUrl = getRecordedVideoUrl(self.name)
#         videoSource = self.getVideoSource()
#         startRecording(videoSource, recordingDir,
#                        recordingUrl, self.start_time,
#                        settings.XGDS_VIDEO_MAX_EPISODE_DURATION_MINUTES,
#                        episode=flightGroup.videoEpisode)

#     def stopVideoRecording(self):
#         stopRecording(self.getVideoSource(), self.end_time)
#         done = True
#         for flight in self.group.basaltflight_set.all():
#             if flight.hasStarted():
#                 if not flight.hasEnded():
#                     done = False
#                     break
#         if done:
#             episode = self.group.videoEpisode
#             episode.endTime = self.end_time
#             episode.save()

    def startFlightExtras(self, request):
        if settings.GEOCAM_TRACK_SERVER_TRACK_PROVIDER:
            self.startTracking()

        # start the video
        if settings.XGDS_VIDEO_ON:
            self.getVideoSource()
            startFlightRecording(request, self.name)

    def stopFlightExtras(self, request):
        #stop the eva track listener
        if settings.GEOCAM_TRACK_SERVER_TRACK_PROVIDER:
            self.stopTracking()
        
        if settings.XGDS_VIDEO_ON:
            stopFlightRecording(request, self.name)
        
    
    def getTreeJsonChildren(self):
        children = super(BasaltFlight, self).getTreeJsonChildren()
        children.append({"title": "Notes", 
                         "selected": False, 
                         "tooltip": "Notes for " + self.name, 
                         "key": self.uuid + "_notes", 
                         "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'XGDS_NOTES_NOTE_MODEL',
                                                                                         'filter': 'flight__pk:'+str(self.pk)}),
                                 "sseUrl": "", 
                                 "type": 'MapLink', 
                                 }
                         })
        children.append({"title": "Photos", 
                         "selected": False, 
                         "tooltip": "Images for " + self.name, 
                         "key": self.uuid + "_images", 
                         "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'XGDS_IMAGE_IMAGE_SET_MODEL',
                                                                                         'filter': 'flight__pk:'+str(self.pk)}),
                                 "sseUrl": "", 
                                 "type": 'MapLink', 
                                 }
                         })
        children.append({"title": "Samples", 
                         "selected": False, 
                         "tooltip": "Samples for " + self.name, 
                         "key": self.uuid + "_samples", 
                         "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'XGDS_SAMPLE_SAMPLE_MODEL',
                                                                                         'filter': 'flight__pk:'+str(self.pk)}),
                                 "sseUrl": "", 
                                 "type": 'MapLink', 
                                 }
                         })
        children.append({"title": "ASD", 
                         "selected": False, 
                         "tooltip": "ASD readings for " + self.name, 
                         "key": self.uuid + "_asd", 
                         "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'basaltApp.AsdDataProduct',
                                                                                         'filter': 'flight__pk:'+str(self.pk)}),
                                 "sseUrl": "", 
                                 "type": 'MapLink', 
                                 }
                         })
        children.append({"title": "FTIR", 
                         "selected": False, 
                         "tooltip": "FTIR readings for " + self.name, 
                         "key": self.uuid + "_ftir", 
                         "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'basaltApp.FtirDataProduct',
                                                                                         'filter': 'flight__pk:'+str(self.pk)}),
                                 "sseUrl": "", 
                                 "type": 'MapLink', 
                                 }
                         })
#         children.append({"title": "pXRF", 
#                          "selected": False, 
#                          "tooltip": "pXRF readings for " + self.name, 
#                          "key": self.uuid + "_pxrf", 
#                          "data": {"json": reverse('xgds_map_server_objectsJson', kwargs={'object_name':'basaltApp.PxrfDataProduct',
#                                                                                          'filter': 'flight__pk:'+str(self.pk)}),
#                                  "sseUrl": "", 
#                                  "type": 'MapLink', 
#                                  }
#                          })
        return children


class BasaltPlanExecution(plannerModels.AbstractPlanExecution):
    ''' 
    A Plan Execution that also includes an EV
    '''
    # set foreign key fields required by parent model to correct types for this site
    flight = models.ForeignKey(BasaltFlight, null=True, blank=True)
    plan = plannerModels.DEFAULT_PLAN_FIELD()

    ev = models.ForeignKey(EV)
    
    def toSimpleDict(self):
        result = super(BasaltPlanExecution, self).toSimpleDict()
        if self.ev:
            result['ev'] = self.ev.pk
        else:
            result['ev'] = None
        return result


class BasaltActiveFlight(plannerModels.AbstractActiveFlight):
    flight = models.OneToOneField(BasaltFlight, related_name="active")
    
    
class Replicate(AbstractEnumModel):
    def __unicode__(self):
        return u'%s' % (self.display_name)


class BasaltSample(xgds_sample_models.AbstractSample):
    # set foreign key fields required by parent model to correct types for this site
    resource = models.ForeignKey(BasaltResource, null=True, blank=True) #, default=BasaltResource.objects.get(name=settings.XGDS_SAMPLE_DEFAULT_COLLECTOR))
    track_position = models.ForeignKey(PastPosition, null=True, blank=True)
    user_position = models.ForeignKey(PastPosition, null=True, blank=True, related_name="sample_user_set" )
    number = models.IntegerField(null=True, verbose_name='Two digit sample location #', db_index=True)
    station_number = models.CharField(null=True, max_length=32, blank=True, verbose_name='Two digit station #', db_index=True)
    replicate = models.ForeignKey(Replicate, null=True, blank=True)
    year = models.PositiveSmallIntegerField(null=True, default=int(timezone.now().strftime("%y")), db_index=True)
    flight = models.ForeignKey(BasaltFlight, null=True, blank=True, verbose_name=settings.XGDS_PLANNER2_FLIGHT_MONIKER)
    marker_id = models.CharField(null=True, blank=True, max_length=32, db_index=True)
    
    @classmethod
    def getSearchableNumericFields(self):
        return ['year', 'number', 'label__number']

    @classmethod
    def getSearchableFields(self):
        result = super(BasaltSample, self).getSearchableFields()
        result.extend(['station_number', 'marker_id','replicate__display_name', 'flight__name'])
        return result

    
    @property
    def flight_name(self):
        if self.flight:
            return self.flight.name
        else:
            return None

    @classmethod
    def getFieldOrder(cls):
        return ['region', 
                'year', 
                'sample_type', 
                'number',
                'station_number',
                'replicate', 
                'collector_name', 
                'collection_time',
                'marker_id',
                'description']
    
    @classmethod
    def getFieldsForName(cls):
        return ['region',
                'year',
                'sample_type',
                'number',
                'station_number',
                'replicate']

    @property
    def replicate_name(self):
        if self.replicate:
            return self.replicate.display_name
        return None 
    
    @property
    def resource_name(self):
        if self.resource:
            return self.resource.name
        return None
    
    def getCurrentNumber(self):
        allSamples = BasaltSample.objects.all()
        numbers = [sample.number for sample in allSamples]
        numbers = [n for n in numbers if n is not None]
        numbers.sort()
        if len(numbers) > 0:
            return int(numbers[-1]) + 1
        else:
            return 0
    
    def buildName(self):
        region = self.region.shortName
        year = str(self.year)
        sampleType = self.sample_type.value
        number = ("%03d" % (int(self.number),))
        
        try:
            stationNum = ("%02d" % (int(self.station_number),))
        except:
            stationNum = self.station_number.strip()
        if self.replicate: 
            replicate = str(self.replicate.value)
        else: 
            replicate = ''
        return region + year + sampleType + '-ST' +  stationNum + '-' + number + replicate
    
    def finish_initialization(self, request):
        self.flight = getFlight(self.collection_time, self.resource.vehicle)
        
    def updateSampleFromName(self, name):
        assert name
        dataDict = {}
        dataDict['region'] = name[:2]
        dataDict['year'] = name[2:4]
        dataDict['type'] = name[4:5]
        dataDict['station_number'] = name[8:10]
        
        if name[-1].isalpha():
            dataDict['replicate'] = name[-1]
            index = len(name) -2
            dataDict['number'] = name[11:index]
        else: 
            dataDict['replicate'] = None 
            dataDict['number'] = name[11:]
        
        self.region = xgds_sample_models.Region.objects.get(shortName = dataDict['region'])
        self.sample_type = xgds_sample_models.SampleType.objects.get(value = dataDict['type'])
        self.number = ("%03d" % (int(dataDict['number']),))
        try:
            self.station_number = ("%02d" % (int(dataDict['station_number']),))
        except:
            self.station_number = dataDict['station_number']
        if dataDict['replicate']: 
            self.replicate = Replicate.objects.get(value=dataDict['replicate'])
        self.year = int(dataDict['year']) 
        self.name = name
        self.save()
    
#     def toMapDict(self):
#         result = xgds_sample_models.AbstractSample.toMapDict(self)
#         if result:
#             result['type'] = 'Sample'
#             result['flight'] = self.flight.id if self.flight else None
#             result['replicate'] = self.replicate.id if self.replicate else None
#             result['marker_id'] = self.marker_id if self.marker_id else None
#         return result
#     
    def __unicode__(self):
        if self.pk:
            return 'basaltSample id=%d' % self.pk
        else: 
            return ''

class BasaltInstrumentDataProduct(AbstractInstrumentDataProduct, NoteLinksMixin, NoteMixin):
    flight = models.ForeignKey(BasaltFlight, null=True, blank=True)
    resource = models.ForeignKey(BasaltResource, null=True, blank=True)

    @classmethod
    def getSearchableFields(self):
        result = super(BasaltInstrumentDataProduct, self).getSearchableFields()
        result.append('flight__name', 'minerals')
        return result
    
    @property
    def ev_name(self):
        if self.resource:
            return self.resource.vehicle.name
        return None

    @property
    def flight_name(self):
        if self.flight:
            return self.flight.name
        else:
            return None

    @property
    def samples(self):
        return []

    @classmethod
    def getDataForm(cls, instrument_name):
        if instrument_name == 'FTIR':
            return FtirDataProduct
        elif instrument_name == 'ASD':
            return AsdDataProduct
        elif instrument_name == 'pXRF':
            return PxrfDataProduct
        else: 
            return None
    
#     def toMapDict(self):
#         result = AbstractInstrumentDataProduct.toMapDict(self)
#         if self.flight:
#             result['flight_name'] = self.flight.name
#         else:
#             result['flight_name'] = ''
#         if self.resource:
#             result['ev_name'] = self.resource.vehicle.name
#         else:
#             result['ev_name'] = ''
#         return result

    def __unicode__(self):
        return "%s: %s, %s, %s, %s (portable), %s (mfg)" % (self.flight, self.resource, 
                                       self.acquisition_time, self.instrument.shortName,
                                       self.portable_mime_type, self.manufacturer_mime_type)
    
    class Meta:
        abstract = True


class FtirDataProduct(BasaltInstrumentDataProduct):
    minerals = models.CharField(max_length=2048, blank=True)
    
    @classmethod
    def getSearchableFields(self):
        return ['name', 'description', 'minerals']
    
    @classmethod
    def cls_type(cls):
        return 'FTIR'
    
    @property
    def samples(self):
        samples = [(s.wavenumber, s.reflectance) for s in self.ftirsample_set.all()]
        return samples
    
#     def toMapDict(self):
#         result = BasaltInstrumentDataProduct.toMapDict(self)
#         if self.minerals:
#             result['minerals'] = self.minerals
#         return result


class AsdDataProduct(BasaltInstrumentDataProduct):
    minerals = models.CharField(max_length=2048, blank=True)

    @classmethod
    def getSearchableFields(self):
        return ['name', 'description', 'minerals']
    
    @classmethod
    def cls_type(cls):
        return 'ASD'
    
    @property
    def samples(self):
        samples = [(s.wavelength, s.absorbance) for s in self.asdsample_set.all()]
        return samples
    
#     def toMapDict(self):
#         result = BasaltInstrumentDataProduct.toMapDict(self)
#         if self.minerals:
#             result['minerals'] = self.minerals
#         return result
    

class PxrfDataProduct(BasaltInstrumentDataProduct):
    pass


class FtirSample(models.Model):
    dataProduct = models.ForeignKey(FtirDataProduct)
    wavenumber = models.FloatField(db_index=True)
    reflectance = models.FloatField(db_index=True)

    class Meta:
        ordering = ['dataProduct', '-wavenumber']
        
    def __unicode__(self):
        return "%s: (%f, %f)" % (self.dataProduct.acquisition_time,
                           self.wavenumber, self.reflectance)


class AsdSample(models.Model):
    dataProduct = models.ForeignKey(AsdDataProduct)
    wavelength = models.FloatField(db_index=True)
    absorbance = models.FloatField(db_index=True)

    class Meta:
        ordering = ['dataProduct', 'wavelength']
        
    def __unicode__(self):
        return "%s: (%f, %f)" % (self.dataProduct.acquisition_time,
                                 self.wavelength, self.absorbance)
    

class BasaltUserSession(AbstractUserSession):
    location = models.ForeignKey(Location)
    resource = models.ForeignKey(plannerModels.Vehicle)
    
    @classmethod
    def getFormFields(cls):
        return ['role',
                'location',
                'resource']
    

class BasaltTaggedNote(AbstractTaggedNote):
    # set foreign key fields required by parent model to correct types for this site
    content_object = models.ForeignKey('BasaltNote')


class BasaltNote(AbstractLocatedNote):
    # Override this to specify a list of related fields
    # to be join-query loaded when notes are listed, as an optimization
    # prefetch for reverse or for many to many.
    prefetch_related_fields = ['tags']

    # select related for forward releationships.  
    select_related_fields = ['author', 'role', 'location', 'flight', 'position']
    
    # set foreign key fields and manager required by parent model to correct types for this site
    position = models.ForeignKey(PastPosition, null=True, blank=True)
    tags = TaggableManager(through=BasaltTaggedNote, blank=True)

    flight = models.ForeignKey(BasaltFlight, null=True, blank=True)
    
    @property
    def flight_name(self):
        if self.flight:
            return self.flight.name
        else:
            return None
    
    def calculateDelayedEventTime(self, event_time):
        try:
            if self.flight.active:
                delayConstant = Constant.objects.get(name="delay")
                return event_time - datetime.timedelta(seconds=int(delayConstant.value))
        except:
            pass
        return self.event_time

#     def toMapDict(self):
#         """
#         Return a reduced dictionary that will be turned to JSON for rendering in a map
#         """
#         result = AbstractLocatedNote.toMapDict(self)
#         result['type'] = 'Note'
#         if self.flight:
#             result['flight'] = self.flight.name
#         else:
#             result['flight'] = ''
#         return result
    
    def getPosition(self):
        # IMPORTANT this should not be used across multitudes of notes, it is designed to be used during construction.
        if not self.position:
            resource = None
            if self.flight:
                if self.flight.vehicle:
                    resource = self.flight.vehicle.basaltresource
            self.position = getClosestPosition(timestamp=self.event_time, resource=resource)
        return self.position


class BasaltImageSet(xgds_image_models.AbstractImageSet):
    # set foreign key fields from parent model to point to correct types
    camera = xgds_image_models.DEFAULT_CAMERA_FIELD()
    track_position = models.ForeignKey(PastPosition, null=True, blank=True )
    exif_position = models.ForeignKey(PastPosition, null=True, blank=True, related_name="%(app_label)s_%(class)s_image_exif_set" )
    user_position = models.ForeignKey(PastPosition, null=True, blank=True, related_name="%(app_label)s_%(class)s_image_user_set" )
    resource = models.ForeignKey(BasaltResource, null=True, blank=True)
    flight = models.ForeignKey(BasaltFlight, null=True, blank=True)

    @classmethod
    def getSearchableFields(self):
        result = super(BasaltImageSet, self).getSearchableFields()
        result.append('flight__name')
        return result
    
    @property
    def flight_name(self):
        if self.flight:
            return self.flight.name
        else:
            return None

    @property
    def resource_name(self):
        if self.resource:
            return self.resource.name
        return None

    def finish_initialization(self, request):
        vehicle = None
        if self.resource:
            vehicle = self.resource.vehicle
            self.flight = getFlight(self.acquisition_time, vehicle)
        
#     def toMapDict(self):
#         """
#         Return a reduced dictionary that will be turned to JSON for rendering in a map
#         """
#         result = xgds_image_models.AbstractImageSet.toMapDict(self)
#         result['type'] = 'Photo'
#         if self.flight:
#             result['flight_name'] = self.flight.name
#         else:
#             result['flight_name'] = ''
#         if self.resource:
#             result['resource_name'] = self.resource.name
#         else:
#             result['resource_name'] = ''
#         return result


class BasaltSingleImage(xgds_image_models.AbstractSingleImage):
    """ This can be used for screenshots or non geolocated images 
    """
    # set foreign key fields from parent model to point to correct types
    imageSet = models.ForeignKey(BasaltImageSet, null=True, related_name="images")


class BasaltStillFrame(AbstractStillFrame):
    flight = models.ForeignKey(BasaltFlight, blank=True)

    @property
    def videoUrl(self):
        return '' #TODO implement

    def __unicode__(self):
        return "%s - %s" % (self.flight.name, self.name)