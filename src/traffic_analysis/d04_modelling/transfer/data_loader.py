import xml.etree.ElementTree as ET
from random import shuffle
from PIL import Image
import numpy as np

from traffic_analysis.d00_utils.load_confs import load_paths, load_credentials
from traffic_analysis.d00_utils.data_loader_s3 import DataLoaderS3
from traffic_analysis.d00_utils.data_retrieval import delete_and_recreate_dir


from enum import Enum
class TransferDataset(Enum):
    detrac = 1
    cvat = 2


class DataLoader(object):

    def __init__(self, datasets, creds, paths):
        self.datasets = datasets
        self.creds = creds
        self.paths = paths
        self.load_mapping = {TransferDataset.detrac: self.load_detrac_data,
                             TransferDataset.cvat: self.load_cvat_data}

        self.data_loader_s3 = DataLoaderS3(s3_credentials=creds[paths['s3_creds']],
                                           bucket_name=paths['bucket_name'])

        return


    def get_train_and_test(self, train_fraction):

        delete_and_recreate_dir(self.paths['temp_annotation'])
        delete_and_recreate_dir(self.paths['temp_raw_images'])
        x, y = self.load_data_from_s3()
        delete_and_recreate_dir(self.paths['temp_annotation'])
        delete_and_recreate_dir(self.paths['temp_raw_images'])

        """
        results = shuffle(results)
        train = results[:int(train_fraction * len(results))]
        test = results[int(train_fraction * len(results)):]
        """

        return #train_x, train_y, test_x, test_y

    def load_data_from_s3(self):

        xs = []
        ys = []

        for dataset in self.datasets:
            x, y = self.load_mapping[dataset]()
            xs += x
            ys += y

        return x, y

    def load_inputs_from_s3(self, test_y, train_y):

        test_x = []
        train_x = []

        return test_x, train_x

    def load_detrac_data(self):

        print('Parsing detrac xmls...')
        y = []
        xml_files = self.data_loader_s3.list_objects(prefix=self.paths['s3_detrac_annotations'])
        for xml_file in xml_files:
            result = self.parse_detrac_xml_file(xml_file)
            if (result):
                y += result

        print('Loading detrac images...')
        x = []
        for labels in y:
            print(labels)
            image_num = labels.split(' ')[0].zfill(5)
            folder = labels.split(' ')[1]
            file_to_download = paths['s3_detrac_images'] + \
                               folder + '/' + \
                               'img' + image_num + '.jpg'
            download_file_to = paths['temp_raw_images'] + \
                               folder + '_' + \
                               image_num + '.jpg'

            self.data_loader_s3.download_file(
                path_of_file_to_download=file_to_download,
                path_to_download_file_to=download_file_to)

            img = Image.open(download_file_to)
            img.load()
            x.append(np.asarray(img, dtype="int32"))

        return x, y

    def parse_detrac_xml_file(self, xml_file):

        path = self.paths['temp_annotation'] + xml_file.split('/')[-1]

        try:
            self.data_loader_s3.download_file(path_of_file_to_download=xml_file,
                                              path_to_download_file_to=path)
        except:
            print("Could not download file " + xml_file)

        root = ET.parse(path).getroot()

        results = []
        # [image_index
        # image_path
        # image_width
        # image_height
        # label_index,
        # x_min,
        # y_min,
        # x_max,
        # y_max]

        im_path = path.split('/')[-1][:-4]
        im_width = 250
        im_height = 250

        for track in root.iter('frame'):

            result = str(track.attrib['num']) + \
                     ' ' + str(im_path) + \
                     ' ' + str(im_width) + \
                     ' ' + str(im_height)

            for frame_obj in track.iter('target'):
                vehicle_type = frame_obj.find('attribute').attrib['vehicle_type']

                left = float(frame_obj.find('box').attrib['left'])
                top = float(frame_obj.find('box').attrib['top'])
                width = float(frame_obj.find('box').attrib['width'])
                height = float(frame_obj.find('box').attrib['height'])

                x_min = left
                y_min = top - height
                x_max = left + width
                y_max = top

                result += ' ' + str(vehicle_type) + \
                          ' ' + str(x_min) + \
                          ' ' + str(y_min) + \
                          ' ' + str(x_max) + \
                          ' ' + str(y_max)

            results.append(result)

        if len(results) > 1:
            return results
        else:
            return None

    def load_cvat_data(self):

        print('Parsing cvat xmls...')
        y = []
        xml_files = self.data_loader_s3.list_objects(prefix=self.paths['s3_cvat_annotations'])
        for xml_file in xml_files:
            result = self.parse_cvat_xml_file(xml_file)
            if(result):
                y += result

        print('Loading cvat videos...')
        x = []
        for labels in y:
            print(labels)
            image_num = labels.split(' ')[0].zfill(5)
            folder = labels.split(' ')[1]
            file_to_download = paths['s3_detrac_images'] + \
                               folder + '/' + \
                               'img' + image_num + '.jpg'
            download_file_to = paths['temp_raw_images'] + \
                               folder + '_' + \
                               image_num + '.jpg'

            self.data_loader_s3.download_file(
                path_of_file_to_download=file_to_download,
                path_to_download_file_to=download_file_to)

            img = Image.open(download_file_to)
            img.load()
            x.append(np.asarray(img, dtype="int32"))

        return x, y

    def parse_cvat_xml_file(self, xml_file):

        path = self.paths['temp_annotation'] + xml_file.split('/')[-1]

        try:
            self.data_loader_s3.download_file(path_of_file_to_download=xml_file,
                                              path_to_download_file_to=path)
        except:
            print("Could not download file " + xml_file)

        root = ET.parse(path).getroot()

        results = []

        im_path = path.split('/')[-1][:-4]
        im_width = 250
        im_height = 250

        frame_dict = {}

        for track in root.iter('track'):
            if track.attrib['label'] == 'vehicle':
                for frame in track.iter('box'):
                    frame_num = frame.attrib['frame']

                    if(frame_num not in frame_dict):
                        frame_dict[frame_num] = str(frame_num) + ' ' + \
                                                str(im_path) + ' ' + \
                                                str(im_width) + ' ' + \
                                                str(im_height)

                    vehicle_type = frame.findall('attribute')[2].text
                    x_min = float(frame.attrib['xtl'])
                    y_min = float(frame.attrib['ybr'])
                    x_max = float(frame.attrib['xbr'])
                    y_max = float(frame.attrib['ytl'])

                    frame_dict[frame_num] += ' ' + str(vehicle_type) + \
                                  ' ' + str(x_min) + \
                                  ' ' + str(y_min) + \
                                  ' ' + str(x_max) + \
                                  ' ' + str(y_max)

        results = []
        for key in frame_dict:
            results.append(frame_dict[key])

        if len(results) > 1:
            return results
        else:
            return None

paths = load_paths()
creds = load_credentials()

dl = DataLoader(datasets=[TransferDataset.detrac, TransferDataset.cvat], creds=creds, paths=paths)
dl.get_train_and_test(.8)

#TODO how should I actually interpret the dimensions?