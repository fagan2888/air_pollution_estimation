import os
import yaml

project_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..','..')
def load_parameters():
    with open(project_dir + '/conf/base/parameters.yml') as f:
        params = yaml.safe_load(f)

    return {**params['data_collection'], **params['modelling'], **params['reporting']}

def load_app_parameters():
    with open(project_dir + '/conf/base/app_parameters.yml') as f:
        params = yaml.safe_load(f)

    return {**params['visualization']}


def load_paths():
    with open(project_dir + '/conf/base/paths.yml') as f:
        paths = yaml.safe_load(f)
        s3_paths = paths['s3_paths']
        local_paths = paths['local_paths']

    for key, val in local_paths.items():
        local_paths[key] = project_dir + '/' + val

    return {**s3_paths, **local_paths}