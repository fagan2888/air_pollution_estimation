from traffic_analysis.d00_utils.load_confs import load_parameters, load_paths, load_credentials
from traffic_analysis.d01_data.collect_video_data import download_camera_meta_data, collect_camera_videos, upload_videos, rename_videos
from multiprocessing import Process

params = load_parameters()
paths = load_paths()
creds = load_credentials()

tfl_camera_api = params['tfl_camera_api']
iterations = params['iterations']
delay = params['delay']
s3_credentials = creds[paths['s3_creds']]

local_video_dir = paths['temp_video']

# download camera data from tfl
download_camera_meta_data(tfl_camera_api=tfl_camera_api,
                          paths=paths,
                          s3_credentials=s3_credentials)
print("Downloaded tfl camera details.")


def collect_camera_videos_fn():
    collect_camera_videos(download_url=params['jamcam_url'],
                          s3_credentials=s3_credentials,
                          paths=paths,
                          iterations=iterations,
                          delay=delay)


def upload_videos_fn():
    upload_videos(paths=paths,
                  iterations=iterations,
                  delay=delay)


def rename_videos_fn():
    rename_videos(paths=paths,
                  params=params,
                  chunk_size=1000)


def run_in_parallel(*fns):
    proc = []
    for fn in fns:
        p = Process(target=fn)
        p.start()
        proc.append(p)
    for p in proc:
        p.join()


print("Running download and upload functions in parallel")
run_in_parallel(collect_camera_videos_fn, upload_videos_fn, rename_videos_fn)