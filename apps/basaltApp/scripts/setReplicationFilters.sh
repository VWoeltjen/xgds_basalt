#! /bin/bash

/home/irg/tungsten/tungsten/tools/tpm update --hosts=boat,shore --repl-svc-applier-filters=replicate --property=replicator.filter.replicate.ignore=xgds_basalt.xgds_video_videosegment,xgds_basalt.xgds_video_videoepisode,xgds_basalt.xgds_core_constant

ssh boat /home/irg/tungsten/tungsten/tools/tpm update --hosts=boat,shore --repl-svc-applier-filters=replicate --property=replicator.filter.replicate.ignore=xgds_basalt.xgds_video_videosegment,xgds_basalt.xgds_video_videoepisode,xgds_basalt.xgds_core_constant