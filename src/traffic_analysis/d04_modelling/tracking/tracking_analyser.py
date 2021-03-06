import time
import gc

import cv2
import numpy as np
import pandas as pd
import tensorflow as tf

from traffic_analysis.d00_utils.bbox_helpers import (bboxcv2_to_bboxcvlib,
                                                     bboxcvlib_to_bboxcv2,
                                                     display_bboxes_on_frame,
                                                     color_bboxes,
                                                     bbox_intersection_over_union)
from traffic_analysis.d00_utils.video_helpers import write_mp4
from traffic_analysis.d04_modelling.traffic_analyser_interface import TrafficAnalyserInterface
from traffic_analysis.d04_modelling.tracking.vehicle_fleet import VehicleFleet
from traffic_analysis.d04_modelling.perform_detection_opencv import detect_objects_cv
from traffic_analysis.d04_modelling.perform_detection_tensorflow import detect_objects_tf
from traffic_analysis.d04_modelling.perform_detection_tensorflow import initialize_tensorflow_model


class TrackingAnalyser(TrafficAnalyserInterface):
    def __init__(self, params: dict, paths: dict, blob_credentials: dict):
        """
        (General parameters):
        selected_labels -- labels which we wish to detect

        Model-specific parameters initialized below:

        (Object detection arguments:)
        detection_model -- specify the name of model you want to use for detection
        detection_implementation -- specify model to use for detection
        detection_frequency -- each detection_frequency num of frames, run obj detection alg again to detect new objs
        detection_confidence_threshold -- conf above which to return label
        detection_nms_threshold -- yolo param

        (Object tracking parameters)
        tracking_model -- specify name of model you want to use for tracking (currently only supports OpenCV trackers)
        iou_threshold -- specify threshold to use to decide whether two detected objs should be considered the same


        (Stop start arguments:)
        iou_convolution_window -- frame window size to perform iou computation on (to get an IOU time
                                  series for each vehicle)
        smoothing_method -- method to smooth the IOU time series for each vehicle
        stop_start_iou_threshold -- threshold to binarize the IOU time series into 0 or 1,denoting "moving" or "stopped"
        """
        super().__init__(params, paths)
        # general settings
        self.params = params
        self.paths = paths
        self.blob_credentials = blob_credentials
        self.selected_labels = params['selected_labels']

        # object detection settings
        self.detection_model = params['detection_model']
        # TODO: to be replaced in transfer learning PR
        self.detection_implementation = params['detection_implementation']
        self.detection_frequency = params['detection_frequency']
        self.detection_confidence_threshold = params['detection_confidence_threshold']
        self.detection_nms_threshold = params['detection_nms_threshold']

        if self.detection_model == 'yolov3_tf':
            self.sess = tf.Session()
            self.model_initializer, self.init_data, self.detection_model = initialize_tensorflow_model(
                params=self.params,
                paths=self.paths,
                s3_credentials=self.blob_credentials,
                sess=self.sess)

        # tracking settings
        self.tracker_type = params['opencv_tracker_type']
        self.trackers = []
        self.iou_threshold = params['iou_threshold']

        # post-processing for stop-starts settings
        self.iou_convolution_window = params['iou_convolution_window']
        self.smoothing_method = params['smoothing_method']
        self.stop_start_iou_threshold = params['stop_start_iou_threshold']

        # speedup settings
        self.skip_no_of_frames = params['skip_no_of_frames']

    def add_tracker(self):
        tracker = self.create_tracker_by_name(
            tracker_type=self.tracker_type)
        if tracker:
            self.trackers.append(tracker)
        return tracker

    def create_tracker_by_name(self, tracker_type: str):
        """Create tracker based on tracker name"""
        tracker_types = {'boosting': cv2.TrackerBoosting_create(),
                         'mil': cv2.TrackerMIL_create(),
                         'kcf': cv2.TrackerKCF_create(),
                         'tld': cv2.TrackerTLD_create(),
                         'medianflow': cv2.TrackerMedianFlow_create(),
                         'goturn': cv2.TrackerGOTURN_create(),
                         'mosse': cv2.TrackerMOSSE_create(),
                         'csrt': cv2.TrackerCSRT_create()}
        try:
            return tracker_types[tracker_type]
        except Exception as e:
            print('Incorrect tracker name')
            print('Available trackers are:')
            print("\n".join(tracker_types.keys()))
            return None

    def determine_new_bboxes(self, bboxes_tracked: list, bboxes_detected: list) -> list:
        """Return the indices of "new" bboxes in bboxes_detected so that a new tracker can be added for that

        Args:
            bboxes_tracked: bboxes which are currently tracked. bboxes should be passed in in format (xmin,ymin,w,h)
            bboxes_detected: bboxes which are newly detected. bboxes should be passed in in format (xmin,ymin,w,h)
            iou_threshold: a detected bbox with iou below the iou_threshold (as compared to all existing, tracked bboxes)
                           will be considered new.
        Returns:
            new_bbox_inds: indices of newly detected bounding boxes
        """

        new_bboxes_inds = set(range(len(bboxes_detected))
                              )  # init with all inds
        old_bboxes_inds = set()
        for i, box_a in enumerate(bboxes_detected):
            # if find a box which has high IOU with an already-tracked box, consider it an old box
            for box_b in bboxes_tracked:
                # format conversion needed
                iou = bbox_intersection_over_union(
                    bboxcv2_to_bboxcvlib(box_a), bboxcv2_to_bboxcvlib(box_b))
                if iou > self.iou_threshold:  # assume bbox has already been tracked and is not new
                    old_bboxes_inds.add(i)  # add to set

        new_bboxes_inds = list(new_bboxes_inds.difference(old_bboxes_inds))
        return new_bboxes_inds

    def add_to_multi_tracker(self,
                             multi_tracker: cv2.MultiTracker,
                             frame: np.ndarray,
                             frame_height: int,
                             frame_width: int,
                             bbox):
        """Add bbox to the multitracker as a new tracker
        """
        try:
            multi_tracker.add(newTracker=self.add_tracker(),
                              image=frame,
                              boundingBox=tuple(bbox))
        except Exception as e:
            # convert bbox
            if self.verbose:
                print(e)
                print(f"bbox is {bbox}")
                print("Retrying with bbox formatting corrections...")

            if (bbox[0] <= bbox[2]) and (bbox[1] <= bbox[3]):
                # format: (xmin, ymin, width, height)
                bbox = bboxcvlib_to_bboxcv2(bbox)

            for i in range(4):
                # correct neg coords
                if bbox[i] < 0:
                    bbox[i] = 0

                # check ind coords don't go outside frame
                if i % 2 == 0: # xmin and width
                    if bbox[i] > frame_width:
                        bbox[i] = frame_width
                else: # ymin and height
                    if bbox[i] > frame_height:
                        bbox[i] = frame_height

            # check sum of coords don't go outside frame
            if bbox[0] + bbox[2] > frame_width:
                bbox[2] = frame_width - bbox[0]

            if bbox[1] + bbox[3] > frame_height:
                bbox[3] = frame_height - bbox[1]

            try:
                multi_tracker.add(newTracker=self.add_tracker(),
                                  image=frame,
                                  boundingBox=tuple(bbox))
            except:
                raise

    def detect_and_track_objects(self,
                                 video: np.ndarray,
                                 video_name: str,
                                 video_time_length=10,
                                 make_video=False,
                                 local_mp4_dir: str = None) -> VehicleFleet:
        """Code to track
        This function will initialize a specified tracking algorithm
        (currently only supports OpenCV's built in multitracker methods) with the specified object
        detection algorithm. Each detection_frequency frames, the object detection will be run again
        to detect any new objects which have entered the frame. A VehicleFleet object is used to track the
        initial detection confidence and label for each vehicle as it is detected, and the updated locations
        of the bounding boxes for each vehicle each frame. The VehicleFleet object also performs IOU computations
        on the stored bounding box information to get counts and stop starts.

        Args:
            video -- np array in format (frame_count,frame_height,frame_width,3)
            video_name -- name of video to run on (include .mp4 extension)
            video_time_length -- specify length of video
            make_video -- if true, will write video to local_mp4_dir with name local_mp4_name_tracked.mp4
            local_mp4_dir -- path to directory to store video in
        Returns:
            fleet -- VehicleFleet object containing bbox history for all vehicles tracked
        """

        start_time = time.time()
        # Create a video capture object to read videos
        n_frames, frame_height, frame_width = video.shape[:3]

        # assumes vid_length in seconds
        video_frames_per_sec = int(n_frames / video_time_length)

        # TODO: Distangle the two parameters for length of tracking without detect and skip frames
        frame_interval = self.skip_no_of_frames + 1
        frame_detection_inds = np.arange(0, n_frames,
                                         max(1, self.skip_no_of_frames * frame_interval))
        frames = video[frame_detection_inds, :, :, :]

        all_bboxes, all_labels, all_confs = self.detect_objects_in_frames(frames)
        bboxes = all_bboxes[0]
        labels = all_labels[0]
        confs = all_confs[0]

        # store info returned above in vehicleFleet object
        fleet = VehicleFleet(bboxes=np.array(bboxes),
                             labels=np.array(labels),
                             confs=np.array(confs),
                             video_name=video_name.replace(".mp4", ""))

        # Create MultiTracker object using bboxes, initialize multitracker
        multi_tracker = cv2.MultiTracker_create()
        for bbox in bboxes:
            self.add_to_multi_tracker(multi_tracker=multi_tracker,
                                      frame=video[0, :, :, :],
                                      frame_height=frame_height,
                                      frame_width=frame_width,
                                      bbox=bbox)

        if make_video:
            processed_video = []

        print(f"The number of frames is {n_frames}")
        previous_frame_index = 0
        # Process video and track objects
        for frame_ind in range(1, n_frames):
            if (frame_ind % frame_interval) and (frame_ind + frame_interval) <= n_frames:
                continue
            frame = video[frame_ind, :, :, :]
            # get updated location of objects in subsequent frames, update fleet obj
            success, bboxes_tracked = multi_tracker.update(
                image=frame)
            for _ in range(frame_ind - previous_frame_index):
                fleet.update_vehicles(np.array(bboxes_tracked))
            previous_frame_index = frame_ind

            if make_video:
                # draw tracked objects
                display_bboxes_on_frame(frame, bboxes_tracked,
                                        color_bboxes(fleet.labels),
                                        fleet.compute_label_confs())

            # every x frames, re-detect boxes
            if frame_ind in frame_detection_inds.tolist():
                ind = int(np.squeeze(
                    np.where(frame_detection_inds == frame_ind)))
                if(ind >= all_bboxes.__len__()):
                    ind = -1
                bboxes_detected = all_bboxes[ind]
                labels_detected = all_labels[ind]
                confs_detected = all_confs[ind]

                # re-initialize MultiTracker
                new_bbox_inds = self.determine_new_bboxes(bboxes_tracked,
                                                          bboxes_detected)

                # update fleet object
                if len(new_bbox_inds) > 0:
                    new_bboxes = [bboxes_detected[i] for i in new_bbox_inds]
                    new_labels = [labels_detected[i] for i in new_bbox_inds]
                    new_confs = [confs_detected[i] for i in new_bbox_inds]

                    fleet.add_vehicles(np.array(new_bboxes),
                                       np.array(new_labels),
                                       np.array(new_confs))

                    # iterate through new bboxes
                    for new_bbox in new_bboxes:
                        self.add_to_multi_tracker(multi_tracker=multi_tracker,
                                                  frame=frame,
                                                  frame_height=frame_height,
                                                  frame_width=frame_width,
                                                  bbox=new_bbox)

            if make_video:
                processed_video.append(frame)

                # code to display video frame by frame while it is being processed
                # cv2.imshow('MultiTracker', frame)
                # # quit on ESC button
                # if cv2.waitKey(1) & 0xFF == 27:  # Esc pressed
                #   break
        if make_video:
            write_mp4(local_mp4_dir=local_mp4_dir,
                      mp4_name=video_name + "_tracked.mp4",
                      video=np.array(processed_video),
                      fps=video_frames_per_sec)

        print(
            f'Run time of tracking analyser for one video is {time.time() - start_time} seconds. \nSkipped {frame_interval-1} frames.\nNumber of frames is {fleet.bboxes.shape[2]}.')
        print('Run time of tracking analyser for one video is %s seconds' %
              (time.time() - start_time))

        return fleet

    def detect_objects_in_frames(self, frames):

        all_bboxes = []
        all_labels = []
        all_confs = []

        if self.detection_model == 'yolov3' or self.detection_model == 'yolov3-tiny':
            for frame in frames:
                bboxes, labels, confs = detect_objects_cv(image_capture=frame,
                                                          params=self.params,
                                                          paths=self.paths,
                                                          blob_credentials=self.blob_credentials,
                                                          selected_labels=self.selected_labels)

                all_bboxes.append(bboxes)
                all_labels.append(labels)
                all_confs.append(confs)

        elif self.detection_model == 'yolov3_tf':
            all_bboxes, all_labels, all_confs = detect_objects_tf(images=frames,
                                                                  paths=self.paths,
                                                                  detection_model=self.detection_model,
                                                                  model_initializer=self.model_initializer,
                                                                  init_data=self.init_data,
                                                                  sess=self.sess,
                                                                  selected_labels=self.selected_labels)

        return all_bboxes, all_labels, all_confs

    def cleanup_on_finish(self):
        if self.detection_model == 'yolov3_tf' or self.detection_model == 'traffic_tf':
            self.sess.close()
            tf.reset_default_graph()
        gc.collect()

    def construct_frame_level_df(self, video_dict) -> pd.DataFrame:
        """Construct frame level df for multiple videos
        Args:
            video_dict: key is video filename, key is np array of videos
        Returns:
            pd Dataframe of all frame level info
        """
        # Check that video doesn't come from in-use camera (some are)
        for video_name in list(video_dict.keys()):
            n_frames = video_dict[video_name].shape[0]
            if n_frames < 75:
                del video_dict[video_name]
                print("Video ", video_name,
                      " has been removed from processing because it may be invalid")

        frame_info_list = []

        if not len(video_dict):
            return None

        for video_name, video in video_dict.items():
            fleet = self.detect_and_track_objects(video, video_name)
            single_frame_level_df = fleet.report_frame_level_info()
            frame_info_list.append(single_frame_level_df)
        return pd.concat(frame_info_list)

    def construct_video_level_df(self, frame_level_df) -> pd.DataFrame:
        """Construct video-level stats table using tracking techniques

        Args:
            frame_level_df -- df returned by above function
        Returns:
            pd DataFrame of all video level info
        """
        if frame_level_df.empty:
            return frame_level_df

        video_info_list = []
        for _, single_frame_level_df in frame_level_df.groupby(['camera_id', 'video_upload_datetime']):
            fleet = VehicleFleet(
                frame_level_df=single_frame_level_df, load_from_pd=True)
            # compute the convolved IOU time series for each vehicle and smooth
            fleet.compute_iou_time_series(interval=self.iou_convolution_window)
            fleet.smooth_iou_time_series(
                smoothing_method=self.smoothing_method)
            # sample plotting options
            # fleet.plot_iou_time_series(fig_dir="data", fig_name="param_tuning", smoothed=True)
            video_level_df = fleet.report_video_level_stats(fleet.compute_counts(),
                                                            *fleet.compute_stop_starts(self.stop_start_iou_threshold))
            video_info_list.append(video_level_df)
        return pd.concat(video_info_list)
