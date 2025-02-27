import sys
import cv2
import random
import math
import numpy as np
import torch
import pdb
# import logging

from torch.utils.data import Dataset

from .utils import (normalize_, color_jittering_, \
                lighting_, random_flip_, random_affine_, clip_bbox_, \
                show_example, random_crop_, resize_image_, valid_affine)

from .utils import convert_examples_to_features, read_examples
from pytorch_pretrained_bert.tokenization import BertTokenizer

import albumentations

class MyAugment:
    def __init__(self) -> None:
        pass

    def __call__(self, img, bbox, phrase):
        imgh,imgw, _ = img.shape
        x, y, w, h = (bbox[0]+bbox[2])/2/imgw, (bbox[1]+bbox[3])/2/imgh, (bbox[2]-bbox[0])/imgw, (bbox[3]-bbox[1])/imgh
        #self.augment_hsv(img)
        # Flip up-down
        if random.random() < 0.5:
            img = np.flipud(img)
            #labels[:, 2] = 1 - labels[:, 2]
            y = 1-y
            phrase = phrase.replace('south','*&^special^&*').replace('north','south').replace('*&^special^&*','north')
        # Flip left-right
        if random.random() < 0.5:
            img = np.fliplr(img)
            #labels[:, 1] = 1 - labels[:, 1]
            x = 1-x
            phrase = phrase.replace('west','*&^special^&*').replace('east','west').replace('*&^special^&*','east')
        #
        new_imgh, new_imgw, _ = img.shape
        assert new_imgh==imgh, new_imgw==imgw
        x, y, w, h = x*imgw, y*imgh, w*imgw, h*imgh

        # Crop image
        iscropped=False
        if random.random() < 0.5:
            left, top, right, bottom = x-w/2, y-h/2, x+w/2, y+h/2
            if left >= new_imgw/2:
                start_cropped_x = random.randint(0, int(0.15*new_imgw))
                img = img[:, start_cropped_x:, :]
                left, right = left - start_cropped_x, right - start_cropped_x
            if right <= new_imgw/2:
                start_cropped_x = random.randint(int(0.85*new_imgw), new_imgw)
                img = img[:, 0:start_cropped_x, :]
            if top >= new_imgh/2:
                start_cropped_y = random.randint(0, int(0.15*new_imgh))
                img = img[start_cropped_y:, :, :]
                top, bottom = top - start_cropped_y, bottom - start_cropped_y
            if bottom <= new_imgh/2:
                start_cropped_y = random.randint(int(0.85*new_imgh), new_imgh)
                img = img[0:start_cropped_y, :, :]
            cropped_imgh, cropped_imgw, _ = img.shape
            left, top, right, bottom = left/cropped_imgw, top/cropped_imgh, right/cropped_imgw, bottom/cropped_imgh
            if cropped_imgh != new_imgh or cropped_imgw != new_imgw:
                img = cv2.resize(img, (new_imgh, new_imgw))
            new_cropped_imgh, new_cropped_imgw, _ = img.shape
            left, top, right, bottom = left*new_cropped_imgw, top*new_cropped_imgh, right*new_cropped_imgw, bottom*new_cropped_imgh 
            x, y, w, h = (left+right)/2, (top+bottom)/2, right-left, bottom-top
            iscropped=True
        #if iscropped:
        #    print((new_imgw, new_imgh))
        #    print((cropped_imgw, cropped_imgh), flush=True)
        #    print('============')
        #print(type(img))
        #draw_bbox = np.array([x-w/2, y-h/2, x+w/2, y+h/2], dtype=int)
        #img_new=bbv.draw_rectangle(img, draw_bbox)
        #cv2.imwrite('tmp/'+str(random.randint(0,5000))+"_"+str(iscropped)+".jpg", img_new)

        new_bbox = [(x-w/2), y-h/2, x+w/2, y+h/2]
        #print(bbox)
        #print(new_bbox)
        #print('---end---')
        return img, np.array(new_bbox, dtype=int), phrase

class Referring(Dataset):
    def __init__(self, db, system_configs, data_aug=True, debug=False, shuffle=False, test=False):
        super(Referring, self).__init__()
        self.test = test
        self._db = db
        self._sys_config = system_configs
        self.lstm = system_configs.lstm
        self.data_rng = system_configs.data_rng
        self.data_aug = data_aug
        self.debug = debug
        self.input_size    = self._db.configs["input_size"]
        #self.output_size   = self._db.configs["output_sizes"] # deleted_by_sunyuxi
        # self.rand_scales   = self._db.configs["rand_scales"]
        self.rand_color    = self._db.configs["random_color"]
        self.random_flip   = self._db.configs["random_flip"]
        self.random_aff    = self._db.configs["random_affine"]
        self.lighting      = self._db.configs["random_lighting"]
        self.query_len     = self._db.configs["max_query_len"]
        self.corpus        = self._db.corpus
        self.tokenizer     = BertTokenizer.from_pretrained('bert-base-uncased', do_lower_case=True)
        self.myaugment = MyAugment()

        if shuffle:
            self._db.shuffle_inds()
        
    def __len__(self):
        return len(self._db.db_inds)

    def _tokenize_phrase(self, phrase):
        return self.corpus.tokenize(phrase, self.query_len)

    def __getitem__(self, k_ind):
        db_ind = self._db.db_inds[k_ind]
        while True:
            # reading images
            image_path = self._db.image_path(db_ind)
            image = cv2.imread(image_path)
                # image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            # else:
            if not image.shape[-1] > 1:
                image = np.stack([image] * 3) # duplicate channel if gray image
            
            # original scale
            original_shape = image.shape
            # reading bbox annnotation
            bbox = self._db.annotation_box(db_ind)
            # reading phrase
            phrase = self._db.phrase(db_ind)
            phrase = phrase.lower()

            if self.data_aug:
                #if self.random_flip and random.random() > 0.5:
                #    image, phrase, bbox = random_flip_(image, phrase, bbox.copy()) # should ensure bbox read-only
                image, bbox, phrase = self.myaugment(image, bbox.copy(), phrase)
                
                # resize images
                #image, bbox = resize_image_(image, bbox.copy(), self.input_size)
                #if self.random_aff:
                #    aff_image, aff_bbox = random_affine_(image, bbox.copy())
                #    if valid_affine(aff_bbox, aff_image.shape[:2]):
                #        # only keep valid_affine
                #        image = aff_image
                #        bbox = aff_bbox

                #if self.debug and k_ind % 5000 == 0:
                #    show_example(image, bbox, phrase, name="input_sample{}".format(k_ind))

                image = image.astype(np.float32) / 255.
                if self.rand_color:
                    color_jittering_(self.data_rng, image)
                    if self.lighting:
                        lighting_(self.data_rng, image, 0.1, self._db.eig_val, self._db.eig_vec)
                normalize_(image, self._db.mean, self._db.std)
            else:   ## should be inference, or specified training
                image, bbox = resize_image_(image, bbox.copy(), self.input_size, \
                    padding_color=tuple((self._db.mean * 255).tolist()))    
                image = image.astype(np.float32) / 255.
                normalize_(image, self._db.mean, self._db.std)
            
            bbox = clip_bbox_(bbox.copy(), image.shape[0:2])

            if not ((bbox[2] - bbox[0] > 0) and (bbox[3] - bbox[1] > 0)):
                # show_example(image, bbox.copy(), phrase, name="failure_case_{}".format(k_ind))
                # if failure, choose next image
                db_ind = random.choice(self._db.db_inds)
                continue
            
            image = image.transpose((2, 0, 1))
            if not self.lstm: # for BERT 
                examples = read_examples(phrase, db_ind)
                features = convert_examples_to_features(examples=examples, \
                    seq_length=self.query_len, tokenizer=self.tokenizer)
                word_id = features[0].input_ids
                word_mask = features[0].input_mask
                if self.test:
                    word_id = torch.tensor(word_id, dtype=torch.long)
                    return image, word_id, original_shape
                else:
                    return image, bbox, word_id, word_mask
            else: # for lstm
                assert(False) # added_by_sunyuxi
                phrase = self._tokenize_phrase(phrase)
                if self.test:
                    return image, phrase, original_shape
                else:
                    return image, phrase, bbox
