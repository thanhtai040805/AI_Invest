"""
Purged Combinatorial K-Fold Cross Validation
Based on Marcos López de Prado's Advances in Financial Machine Learning.
Designed to prevent information leakage in time-series panel data.
"""

import numpy as np
import pandas as pd
from typing import Generator, Tuple, List, Union, Optional
from itertools import combinations
import logging

logger = logging.getLogger(__name__)

class PurgedCombinatorialKFold:
    """
    Purged Combinatorial K-Fold cross-validator for time series data.
    
    Generates train/test indices while ensuring no overlap between
    train and test observation windows, and applying an embargo period
    after the test set to prevent leakage via serial correlation.
    
    Parameters
    ----------
    n_splits : int, default=5
        Number of splits (K).
    n_test_splits : int, default=2
        Number of splits used as test set simultaneously.
    embargo_td : pd.Timedelta, default=pd.Timedelta(days=3)
        Embargo period to drop training data immediately following a test set.
        For HOSE T+2, a minimum of 3 days is recommended.
    """
    
    def __init__(
        self, 
        n_splits: int = 5, 
        n_test_splits: int = 2, 
        embargo_td: pd.Timedelta = pd.Timedelta(days=3)
    ):
        if n_splits <= n_test_splits:
            raise ValueError("n_splits must be strictly greater than n_test_splits")
            
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.embargo_td = embargo_td
        
    def split(
        self, 
        X: pd.DataFrame, 
        y: Optional[pd.Series] = None, 
        t1: Optional[pd.Series] = None
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """
        Generate indices to split data into training and test set.
        
        Args:
            X: Features. Must have a DateTimeIndex as level 0 (if MultiIndex) 
               or simply a DateTimeIndex.
            y: Target variable.
            t1: Series of timestamps representing the end of each observation window.
                If None, assumes the window ends at the current index timestamp.
        """
        # Extract unique dates from the index
        if isinstance(X.index, pd.MultiIndex):
            dates = X.index.get_level_values(0)
        else:
            dates = X.index
            
        unique_dates = pd.Series(dates).drop_duplicates().sort_values().values
        
        if len(unique_dates) < self.n_splits:
            raise ValueError(f"Cannot split {len(unique_dates)} unique dates into {self.n_splits} splits")
            
        # If t1 is not provided, we assume the observation ends exactly when it starts
        if t1 is None:
            t1 = pd.Series(dates, index=dates)
            
        # Split dates into K approximately equal blocks
        indices = np.arange(len(unique_dates))
        split_indices = np.array_split(indices, self.n_splits)
        blocks = [unique_dates[idx] for idx in split_indices]
        
        # Iterate over all combinations of test blocks
        for test_combo in combinations(range(self.n_splits), self.n_test_splits):
            test_combo = set(test_combo)
            train_combo = set(range(self.n_splits)) - test_combo
            
            # Combine test dates
            test_dates_list = [blocks[i] for i in test_combo]
            test_dates = np.concatenate(test_dates_list)
            
            # Find the min and max dates for the test blocks
            # Since we can have multiple test blocks, we need to apply purge/embargo
            # to each test block individually.
            train_dates = set()
            for i in train_combo:
                for d in blocks[i]:
                    train_dates.add(d)
            train_dates = pd.DatetimeIndex(sorted(list(train_dates)))
            
            # Purge and Embargo
            for i in test_combo:
                test_block = pd.DatetimeIndex(blocks[i])
                block_start = test_block.min()
                block_end = test_block.max()
                
                # 1. Purge: Find training samples whose observation window (t1) overlaps with test_block
                # Overlap means: train_start <= test_block_end AND train_end >= test_block_start
                # We need to remove these from train_dates
                
                # Efficient vectorization for purging
                # Get t1 for all remaining train dates
                train_t1 = t1.loc[train_dates]
                if isinstance(train_t1, pd.DataFrame) or isinstance(train_t1, pd.Series) and train_t1.index.duplicated().any():
                   # If multiple items per date, get max t1 per date
                   train_t1 = train_t1.groupby(train_t1.index).max()
                   if isinstance(train_t1, pd.DataFrame):
                       train_t1 = train_t1.iloc[:, 0]
                       
                train_t0 = pd.DatetimeIndex(train_t1.index)
                train_t1_vals = pd.DatetimeIndex(train_t1.values)
                
                # Overlap condition
                overlap_mask = (train_t0 <= block_end) & (train_t1_vals >= block_start)
                overlapping_dates = train_t0[overlap_mask]
                
                # 2. Embargo: Remove training samples that start within embargo_td after test_block_end
                embargo_end = block_end + self.embargo_td
                embargo_mask = (train_t0 > block_end) & (train_t0 <= embargo_end)
                embargo_dates = train_t0[embargo_mask]
                
                # Drop overlapping and embargoed dates
                to_drop = overlapping_dates.union(embargo_dates)
                train_dates = train_dates.difference(to_drop)
                
            # Convert final dates to positional indices in X
            train_idx_mask = dates.isin(train_dates)
            test_idx_mask = dates.isin(test_dates)
            
            yield np.where(train_idx_mask)[0], np.where(test_idx_mask)[0]
            
    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        """Returns the number of splitting iterations in the cross-validator."""
        # C(n_splits, n_test_splits)
        import math
        return math.comb(self.n_splits, self.n_test_splits)

def get_purged_train_times(
    t_events: pd.Series, 
    test_times: pd.Series
) -> pd.Series:
    """
    Given a series of testing times, return training times that do not overlap.
    A simpler version if not using K-fold (e.g. simple train/test split).
    """
    trn = t_events.copy(deep=True)
    for test_start, test_end in test_times.items():
        # Train starts within test
        df0 = trn[(test_start <= trn.index) & (trn.index <= test_end)].index
        # Train ends within test
        df1 = trn[(test_start <= trn) & (trn <= test_end)].index
        # Train envelops test
        df2 = trn[(trn.index <= test_start) & (test_end <= trn)].index
        trn = trn.drop(df0.union(df1).union(df2))
    return trn
