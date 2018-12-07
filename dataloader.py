import numpy as np
import scipy.sparse as sp
import os
import h5py
import pandas as pd

import torch
from data_utiles import download_dataset, map_data
from torch.utils.data import Dataset, DataLoader


class MovieLensDataset(Dataset):
    """MovieLens dataset."""

    def __init__(self, dataset="ml_100k", transform=None, testing=False):
        """
        Loads official train/test split and uses 10% of training samples for validaiton
        For each split computes 1-of-num_classes labels. Also computes training
        adjacency matrix. Assumes flattening happens everywhere in row-major fashion.
        """
        sep = '\t'

        #check if file exist and download otherwise
        files = ['/u1.base', '/u1.test', '/u.item', '/u.user']
        fname = dataset
        data_dir = 'data/' + fname

        download_dataset(fname, files, data_dir)

        dtypes = {
            'u_nodes': np.int32, 
            'v_nodes': np.int32,
            'ratings': np.float32, 
            'timestanp': np.float32
        }

        filename_train = 'data/' + dataset + '/u1.base'
        filename_test = 'data/' + dataset + '/u1.test'

        data_train = pd.read_csv(
            filename_train, sep=sep, header=None,
            names=['u_nodes', 'v_nodes', 'raitings', 'timestamp'], dtype=dtypes)
    
        data_test = pd.read_csv(
            filename_test, sep=sep, header=None,
            names=['u_nodes', 'v_nodes', 'raitings', 'timestamp'], dtype=dtypes)

        data_array_train = data_train.values.tolist()
        data_array_train = np.array(data_array_train)
        data_array_test = data_test.values.tolist()
        data_array_test = np.array(data_array_test)

        data_array = np.concatenate([data_array_train, data_array_test], axis=0)

        u_nodes = data_array[:, 0].astype(dtypes['u_nodes'])
        v_nodes = data_array[:, 1].astype(dtypes['v_nodes'])
        ratings = data_array[:, 2].astype(dtypes['ratings'])

        # Map data to proper indices in case they are not in a continues [0, N) range
        u_nodes, u_dict, num_users = map_data(u_nodes)
        v_nodes, v_dict, num_items = map_data(v_nodes)

        u_nodes = u_nodes.astype(np.int64)
        v_nodes = v_nodes.astype(np.int64)
        ratings = ratings.astype(np.float64)


        rating_dict = {r: i for i, r in enumerate(np.sort(np.unique(ratings)))}
   
        # neutral_rating = -1
        labels = np.full((num_users, num_items), -1, dtype=np.int32)
        labels[u_nodes, v_nodes] = np.array([rating_dict[r] for r in ratings])

        for i in range(len(u_nodes)):
            assert(labels[u_nodes[i], v_nodes[i]] == rating_dict[ratings[i]])

        labels = labels.reshape([-1])

        # number of test and validation edges, see cf-node code

        num_train = data_array_train.shape[0]
        num_test = data_array_test.shape[0]
        num_val = int(np.ceil(num_train * 0.2))
        num_train = num_train - num_val

        pairs_nonzero = np.array([[u,v] for u, v in zip(u_nodes, v_nodes)])
        idx_nonzero = np.array([u * num_items + v for u, v in pairs_nonzero])
    
        for i in range(len(ratings)):
            assert(labels[idx_nonzero[i]] == rating_dict[ratings[i]])

        idx_nonzero_train = idx_nonzero[0:num_train+num_val]
        idx_nonzero_test = idx_nonzero[num_train+num_val:]

        pairs_nonzero_train = pairs_nonzero[0:num_train+num_val]
        pairs_nonzero_test = pairs_nonzero[num_train+num_val:]

        # Internally shuffle training set (before splitting off validation set)
        rand_idx = list(range(len(idx_nonzero_train)))
        np.random.seed(42)
        np.random.shuffle(rand_idx)
        idx_nonzero_train = idx_nonzero_train[rand_idx]
        pairs_nonzero_train = pairs_nonzero_train[rand_idx]

        idx_nonzero = np.concatenate([idx_nonzero_train, idx_nonzero_test], axis=0)
        pairs_nonzero = np.concatenate([pairs_nonzero_train, pairs_nonzero_test], axis=0)

        val_idx = idx_nonzero[0:num_val]
        train_idx = idx_nonzero[num_val:num_train + num_val]
        test_idx = idx_nonzero[num_train + num_val:]

        assert(len(test_idx) == num_test)

        val_pairs_idx = pairs_nonzero[0:num_val]
        train_pairs_idx = pairs_nonzero[num_val:num_train + num_val]
        test_pairs_idx = pairs_nonzero[num_train + num_val:]

        u_test_idx, v_test_idx = test_pairs_idx.transpose()
        u_val_idx, v_val_idx = val_pairs_idx.transpose()
        u_train_idx, v_train_idx = train_pairs_idx.transpose()

        # create labels
        train_labels = labels[train_idx]
        val_labels = labels[val_idx]
        test_labels = labels[test_idx]

        '''
        if testing:
            u_train_idx = np.hstack([u_train_idx, u_val_idx])
            v_train_idx = np.hstack([v_train_idx, v_val_idx])
            train_labels = np.hstack([train_labels, val_labels])
            # for adjacency matrix construction
            train_idx = np.hstack([train_idx, val_idx])
        '''
    
        # make training adjacency matrix
        rating_mx_train = np.zeros(num_users * num_items, dtype=np.float32)
        rating_mx_train[train_idx] = labels[train_idx].astype(np.float32) + 1.
        #rating_mx_train = sp.csr_matrix(rating_mx_train.reshape(num_users, num_items))
        rating_mx_train = rating_mx_train.reshape(num_users, num_items)

        class_values = np.sort(np.unique(ratings))

        if dataset == 'ml_100k':
        
            # movie features (genres)
            sep = r'|'
            movie_file = 'data/' + dataset + '/u.item'
            movie_headers = ['movie id', 'movie title', 'release date', 'video release date',
                            'IMDb URL', 'unknown', 'Action', 'Adventure', 'Animation',
                            'Childrens', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Fantasy',
                            'Film-Noir', 'Horror', 'Musical', 'Mystery', 'Romance', 'Sci-Fi',
                            'Thriller', 'War', 'Western']
            movie_df = pd.read_csv(movie_file, sep=sep, header=None,
                                names=movie_headers, engine='python')
                                
            genre_headers = movie_df.columns.values[6:]
            num_genres = genre_headers.shape[0]

            v_features = np.zeros((num_items, num_genres), dtype=np.float32)
            for movie_id, g_vec in zip(movie_df['movie id'].values.tolist(), movie_df[genre_headers].values.tolist()):
                # check if movie_id was listed in ratings file and therefore in mapping dictionary
                if movie_id in v_dict.keys():
                    v_features[v_dict[movie_id], :] = g_vec
            

            # user features
            sep = r'|'
            users_file = 'data/' + dataset + '/u.user'
            users_headers = ['user id', 'age', 'gender', 'occupation', 'zip code']
            users_df = pd.read_csv(users_file, sep=sep, header=None,
                                names=users_headers, engine='python')
            
            occupation = set(users_df['occupation'].values.tolist())

            age = users_df['age'].values
            age_max = age.max()

            gender_dict = {'M': 0., 'F': 1.}
            occupation_dict = {f: i for i, f in enumerate(occupation, start=2)}

            num_feats = 2 + len(occupation_dict)

            u_features = np.zeros((num_users, num_feats), dtype=np.float32)
            for _, row in users_df.iterrows():
                u_id = row['user id']
                if u_id in u_dict.keys():
                    # age
                    u_features[u_dict[u_id], 0] = row['age'] / np.float(age_max)
                    # gender
                    u_features[u_dict[u_id], 1] = gender_dict[row['gender']]
                    # occupation
                    u_features[u_dict[u_id], occupation_dict[row['occupation']]] = 1


        elif dataset == 'ml_1m':

            # load movie features
            movies_file = 'data/' + dataset + '/movies.dat'

            movies_headers = ['movie_id', 'title', 'genre']
            movies_df = pd.read_csv(movies_file, sep=sep, header=None,
                                names=movies_headers, engine='python')

            # extracting all genres
            genres = []
            for s in movies_df['genre'].values:
                genres.extend(s.split('|'))

            enres = list(set(genres))
            num_genres = len(genres)

            genres_dict = {g: idx for idx, g in enumerate(genres)}

            # creating 0 or 1 valued features for all genres
            v_features = np.zeros((num_items, num_genres), dtype=np.float32)
            for movie_id, s in zip(movies_df['movie_id'].values.tolist(), movies_df['genre'].values.tolist()):
                # check if movie_id was listed in ratings file and therefore in mapping dictionary
                if movie_id in v_dict.keys():
                    gen = s.split('|')
                    for g in gen:
                        v_features[v_dict[movie_id], genres_dict[g]] = 1.

            # load user features
            users_file = 'data/' + dataset + '/users.dat'
            users_headers = ['user_id', 'gender', 'age', 'occupation', 'zip-code']
            users_df = pd.read_csv(users_file, sep=sep, header=None,
                                   names=users_headers, engine='python')

            # extracting all features
            cols = users_df.columns.values[1:]

            cntr = 0
            feat_dicts = []
            for header in cols:
                d = dict()
                feats = np.unique(users_df[header].values).tolist()
                d.update({f: i for i, f in enumerate(feats, start=cntr)})
                feat_dicts.append(d)
                cntr += len(d)

            num_feats = sum(len(d) for d in feat_dicts)

            u_features = np.zeros((num_users, num_feats), dtype=np.float32)
            for _, row in users_df.iterrows():
                u_id = row['user_id']
                if u_id in u_dict.keys():
                    for k, header in enumerate(cols):
                        u_features[u_dict[u_id], feat_dicts[k][row[header]]] = 1.
                        
        else:
            raise ValueError('Invalid dataset option %s' % dataset)



        #u_features = sp.csr_matrix(u_features)
        #v_features = sp.csr_matrix(v_features)

        print("User features shape: "+str(u_features.shape))
        print("Item features shape: "+str(v_features.shape))

        self.u_features = u_features 
        self.v_features = v_features
        self.adj_matraix = rating_mx_train
        self.train_labels = train_labels
        self.u_train_idx = u_train_idx
        self.v_train_idx = v_train_idx
        self.val_labels = val_labels
        self.u_val_idx = u_val_idx
        self.v_val_idx = v_val_idx
        self.test_labels = test_labels
        self.u_test_idx = u_test_idx
        self.v_test_idx = v_test_idx
        self.class_values = class_values
        self.transform = transform
        

    def __len__(self):
        return self.adj_matraix.shape[0]

    
    def __getitem__(self,idx):  
        u_perchase = self.adj_matraix[idx]
        u_feature = self.u_features[idx]
        v_feature = self.v_features
        sample = {'u_perchase': u_perchase, 'u_feature': u_feature, 'v_feature': v_feature}

        if self.transform:
            sample = self.transform(sample)
        
        return sample


class ToTensor(object):
    """Convert ndarrays in sample to Tensors."""

    def __call__(self, sample):
        u_perchase, u_feature, v_feature = sample['u_perchase'], sample['u_feature'], sample['v_feature']

        # swap color axis because
        # numpy image: H x W x C
        # torch image: C X H X W
        return {'u_perchase': torch.from_numpy(u_perchase),
                'u_feature': torch.from_numpy(u_feature),
                'v_feature': torch.from_numpy(v_feature)}