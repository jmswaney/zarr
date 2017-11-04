# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division
import numbers


import numpy as np


def replace_ellipsis(selection, shape):

    # count number of ellipsis present
    n_ellipsis = sum(1 for i in selection if i is Ellipsis)

    if n_ellipsis > 1:
        # more than 1 is an error
        raise IndexError("an index can only have a single ellipsis ('...')")

    elif n_ellipsis == 1:
        # locate the ellipsis, count how many items to left and right
        n_items_l = selection.index(Ellipsis)  # items to left of ellipsis
        n_items_r = len(selection) - (n_items_l + 1)  # items to right of ellipsis
        n_items = len(selection) - 1  # all non-ellipsis items

        if n_items >= len(shape):
            # ellipsis does nothing, just remove it
            selection = tuple(i for i in selection if i != Ellipsis)

        else:
            # replace ellipsis with as many slices are needed for number of dims
            new_item = selection[:n_items_l] + ((slice(None),) * (len(shape) - n_items))
            if n_items_r:
                new_item += selection[-n_items_r:]
            selection = new_item

    # fill out selection if not completely specified
    if len(selection) < len(shape):
        selection += tuple(slice(0, l) for l in shape[len(selection):])

    return selection


class OIndex(object):

    def __init__(self, array):
        self.array = array

    def __getitem__(self, selection):
        return self.array.get_orthogonal_selection(selection)

    def __setitem__(self, selection, value):
        return self.array.set_orthogonal_selection(selection, value)


def is_coordinate_selection(selection, array):
    return (
        (len(selection) == array.ndim) and
        all(
            [(isinstance(dim_sel, numbers.Integral) or
             (hasattr(dim_sel, 'dtype') and dim_sel.dtype.kind in 'ui'))
             for dim_sel in selection]
        )
    )


def is_mask_selection(selection, array):
    return (
        hasattr(selection, 'dtype') and
        selection.dtype == bool and
        hasattr(selection, 'shape') and
        len(selection.shape) == len(array.shape)
    )


def replace_lists(selection):
    return tuple(
        np.asarray(dim_sel) if isinstance(dim_sel, list) else dim_sel
        for dim_sel in selection
    )


class VIndex(object):

    def __init__(self, array):
        self.array = array

    def __getitem__(self, selection):
        if not isinstance(selection, tuple):
            selection = tuple(selection)
        selection = replace_lists(selection)
        if is_coordinate_selection(selection, self.array):
            return self.array.get_coordinate_selection(selection)
        # elif is_mask_selection(selection, self.array):
        #     return self.array.get_mask_selection(selection)
        else:
            raise IndexError('unsupported selection')

    # def __setitem__(self, selection, value):
    #     return self.array.set_orthogonal_selection(selection, value)


def normalize_integer_selection(dim_sel, dim_len):

    # normalize type to int
    dim_sel = int(dim_sel)

    # handle wraparound
    if dim_sel < 0:
        dim_sel = dim_len + dim_sel

    # handle out of bounds
    if dim_sel >= dim_len or dim_sel < 0:
        raise IndexError('index out of bounds: %s' % dim_sel)

    return dim_sel


def normalize_slice_selection(dim_sel, dim_len):

    # handle slice with None bound
    start = 0 if dim_sel.start is None else dim_sel.start
    stop = dim_len if dim_sel.stop is None else dim_sel.stop
    step = 1 if dim_sel.step is None else dim_sel.step

    # handle wraparound
    if start < 0:
        start = dim_len + start
    if stop < 0:
        stop = dim_len + stop

    # handle out of bounds
    if start < 0:
        raise IndexError('start index out of bounds: %s' % dim_sel.start)
    if stop < 0:
        raise IndexError('stop index out of bounds: %s' % dim_sel.stop)
    if start >= dim_len and dim_len > 0:
        raise IndexError('start index out of bounds: %ss' % dim_sel.start)
    if stop > dim_len:
        stop = dim_len
    if stop < start:
        stop = start

    return slice(start, stop, step)


class IndexerBase(object):

    def __init__(self, selection, array):
        self.selection = selection
        self.array = array
        self.squeeze_axes = None

    def __iter__(self):
        return iter(self.selection)

    def __len__(self):
        return len(self.selection)


# noinspection PyProtectedMember
class BasicIndexer(IndexerBase):

    def __init__(self, selection, array):

        # ensure tuple
        if not isinstance(selection, tuple):
            selection = (selection,)

        # handle ellipsis
        selection = replace_ellipsis(selection, array._shape)

        # validation - check dimensionality
        if len(selection) > len(array._shape):
            raise IndexError('too many indices for array')
        if len(selection) < len(array._shape):
            raise IndexError('not enough indices for array')

        # TODO refactor with OrthogonalIndexer

        # normalization
        selection = self.normalize_selection(selection, array)

        # complete initialisation
        super(BasicIndexer, self).__init__(selection, array)

    def normalize_selection(self, selection, array):
        # normalize each dimension
        selection = tuple(self.normalize_dim_selection(s, l)
                          for s, l in zip(selection, array._shape))
        return selection

    def normalize_dim_selection(self, dim_sel, dim_len):

        if isinstance(dim_sel, numbers.Integral):

            dim_sel = normalize_integer_selection(dim_sel, dim_len)
            return dim_sel

        elif isinstance(dim_sel, slice):

            dim_sel = normalize_slice_selection(dim_sel, dim_len)

            # handle slice with step
            if dim_sel.step is not None and dim_sel.step != 1:
                raise IndexError('slice with step not supported via basic indexing; use '
                                 'orthogonal indexing instead')

            return dim_sel

        else:
            raise IndexError('unsupported index item type: %r' % dim_sel)

    def get_overlapping_chunks(self):
        """Convenience function to find chunks overlapping an array selection. N.B.,
        assumes selection has already been normalized."""

        # indices of chunks overlapping the selection
        chunk_ranges = []

        # shape of the selection
        sel_shape = []

        # iterate over dimensions of the array
        for dim_sel, dim_chunk_len in zip(self.selection, self.array._chunks):

            # dim_sel: selection for current dimension
            # dim_chunk_len: length of chunk along current dimension

            dim_sel_len = None

            if isinstance(dim_sel, int):

                # dim selection is an integer, i.e., single item, so only need single chunk index
                # for this dimension
                dim_chunk_range = [dim_sel//dim_chunk_len]

            elif isinstance(dim_sel, slice):

                # dim selection is a slice, need range of chunk indices including start and stop of
                # selection
                dim_chunk_from = dim_sel.start//dim_chunk_len
                dim_chunk_to = int(np.ceil(dim_sel.stop/dim_chunk_len))
                dim_chunk_range = range(dim_chunk_from, dim_chunk_to)
                dim_sel_len = dim_sel.stop - dim_sel.start

            else:
                raise RuntimeError('unexpected selection type')

            chunk_ranges.append(dim_chunk_range)
            if dim_sel_len is not None:
                sel_shape.append(dim_sel_len)

        return chunk_ranges, tuple(sel_shape)

    def get_chunk_projection(self, chunk_coords):

        # chunk_coords: holds the index along each dimension for the current chunk within the
        # chunk grid. E.g., (0, 0) locates the first (top left) chunk in a 2D chunk grid.

        chunk_selection = []
        out_selection = []

        # iterate over dimensions (axes) of the array
        for dim_sel, dim_chunk_idx, dim_chunk_len in zip(self.selection, chunk_coords,
                                                         self.array._chunks):

            # dim_sel: selection for current dimension
            # dim_chunk_idx: chunk index along current dimension
            # dim_chunk_len: chunk length along current dimension

            # selection into output array to store data from current chunk
            dim_out_sel = None

            # calculate offset for current chunk along current dimension - this is used to
            # determine the values to be extracted from the current chunk
            dim_chunk_offset = dim_chunk_idx * dim_chunk_len

            # handle integer selection, i.e., single item
            if isinstance(dim_sel, int):

                dim_chunk_sel = dim_sel - dim_chunk_offset

                # N.B., leave dim_out_sel as None, as this dimension has been dropped in the
                # output array because of single value index

            # handle slice selection, i.e., contiguous range of items
            elif isinstance(dim_sel, slice):

                if dim_sel.start <= dim_chunk_offset:
                    # selection starts before current chunk
                    dim_chunk_sel_start = 0
                    dim_out_offset = dim_chunk_offset - dim_sel.start

                else:
                    # selection starts within current chunk
                    dim_chunk_sel_start = dim_sel.start - dim_chunk_offset
                    dim_out_offset = 0

                if dim_sel.stop > dim_chunk_offset + dim_chunk_len:
                    # selection ends after current chunk
                    dim_chunk_sel_stop = dim_chunk_len

                else:
                    # selection ends within current chunk
                    dim_chunk_sel_stop = dim_sel.stop - dim_chunk_offset

                dim_chunk_sel = slice(dim_chunk_sel_start, dim_chunk_sel_stop)
                dim_chunk_nitems = dim_chunk_sel_stop - dim_chunk_sel_start
                dim_out_sel = slice(dim_out_offset, dim_out_offset + dim_chunk_nitems)

                # TODO refactor code with OrthogonalIndexer

            else:
                raise RuntimeError('unexpected selection type')

            # add to chunk selection
            chunk_selection.append(dim_chunk_sel)

            # add to output selection
            if dim_out_sel is not None:
                out_selection.append(dim_out_sel)

        # normalise for indexing into numpy arrays
        chunk_selection = tuple(chunk_selection)
        out_selection = tuple(out_selection)

        return chunk_selection, out_selection


# noinspection PyProtectedMember
class OrthogonalIndexer(IndexerBase):

    def __init__(self, selection, array):

        # ensure tuple
        if not isinstance(selection, tuple):
            selection = (selection,)

        # handle ellipsis
        selection = replace_ellipsis(selection, array._shape)

        # validation - check dimensionality
        if len(selection) > len(array._shape):
            raise IndexError('too many indices for array')
        if len(selection) < len(array._shape):
            raise IndexError('not enough indices for array')

        # normalization
        selection = self.normalize_selection(selection, array)

        # super initialisation
        super(OrthogonalIndexer, self).__init__(selection, array)

        # figure out if we're going to be doing advanced indexing on chunks, if so then
        # chunk selections will need special handling
        self.is_advanced = any([not isinstance(dim_sel, (int, slice))
                                for dim_sel in selection])

        # locate axes that need to get squeezed out later if doing advanced selection
        if self.is_advanced:
            self.squeeze_axes = tuple([i for i, dim_sel in enumerate(selection)
                                       if isinstance(dim_sel, int)])
        else:
            self.squeeze_axes = None

    def normalize_selection(self, selection, array):
        # normalize each dimension
        selection = tuple(self.normalize_dim_selection(s, l, c)
                          for s, l, c in zip(selection, array._shape, array._chunks))
        return selection

    def normalize_dim_selection(self, dim_sel, dim_len, dim_chunk_len):

        # normalize list to array
        if isinstance(dim_sel, list):
            dim_sel = np.asarray(dim_sel)

        if isinstance(dim_sel, numbers.Integral):

            dim_sel = normalize_integer_selection(dim_sel, dim_len)
            return dim_sel

        elif isinstance(dim_sel, slice):

            dim_sel = normalize_slice_selection(dim_sel, dim_len)

            # handle slice with step
            if dim_sel.step != 1:
                dim_sel = np.arange(dim_sel.start, dim_sel.stop, dim_sel.step)
                return IntArrayDimSelection(dim_sel, dim_len, dim_chunk_len)

            return dim_sel

        elif hasattr(dim_sel, 'dtype') and hasattr(dim_sel, 'shape'):

            if dim_sel.dtype == bool:
                return BoolArrayDimSelection(dim_sel, dim_len, dim_chunk_len)

            elif dim_sel.dtype.kind in 'ui':
                return IntArrayDimSelection(dim_sel, dim_len, dim_chunk_len)

            else:
                raise IndexError('unsupported index item type: %r' % dim_sel)

        else:
            raise IndexError('unsupported index item type: %r' % dim_sel)

    def get_overlapping_chunks(self):
        """Convenience function to find chunks overlapping an array selection. N.B.,
        assumes selection has already been normalized."""

        # indices of chunks overlapping the selection
        chunk_ranges = []

        # shape of the selection
        sel_shape = []

        # iterate over dimensions of the array
        for dim_sel, dim_chunk_len in zip(self.selection, self.array._chunks):

            # dim_sel: selection for current dimension
            # dim_chunk_len: length of chunk along current dimension

            dim_sel_len = None

            if isinstance(dim_sel, int):

                # dim selection is an integer, i.e., single item, so only need single chunk index for
                # this dimension
                dim_chunk_range = [dim_sel//dim_chunk_len]

            elif isinstance(dim_sel, slice):

                # dim selection is a slice, need range of chunk indices including start and stop of
                # selection
                dim_chunk_from = dim_sel.start//dim_chunk_len
                dim_chunk_to = int(np.ceil(dim_sel.stop/dim_chunk_len))
                dim_chunk_range = range(dim_chunk_from, dim_chunk_to)
                dim_sel_len = dim_sel.stop - dim_sel.start

            elif isinstance(dim_sel, BoolArrayDimSelection):

                # dim selection is a boolean array, delegate this to the BooleanSelection class
                dim_chunk_range = dim_sel.get_overlapping_chunks()
                dim_sel_len = dim_sel.nitems

            elif isinstance(dim_sel, IntArrayDimSelection):

                # dim selection is an integer array, delegate this to the integerSelection class
                dim_chunk_range = dim_sel.get_overlapping_chunks()
                dim_sel_len = dim_sel.nitems

            else:
                raise RuntimeError('unexpected selection type')

            chunk_ranges.append(dim_chunk_range)
            if dim_sel_len is not None:
                sel_shape.append(dim_sel_len)

        return chunk_ranges, tuple(sel_shape)

    def get_chunk_projection(self, chunk_coords):

        # chunk_coords: holds the index along each dimension for the current chunk within the
        # chunk grid. E.g., (0, 0) locates the first (top left) chunk in a 2D chunk grid.

        chunk_selection = []
        out_selection = []

        # iterate over dimensions (axes) of the array
        for dim_sel, dim_chunk_idx, dim_chunk_len in zip(self.selection, chunk_coords,
                                                         self.array._chunks):

            # dim_sel: selection for current dimension
            # dim_chunk_idx: chunk index along current dimension
            # dim_chunk_len: chunk length along current dimension

            # selection into output array to store data from current chunk
            dim_out_sel = None

            # calculate offset for current chunk along current dimension - this is used to
            # determine the values to be extracted from the current chunk
            dim_chunk_offset = dim_chunk_idx * dim_chunk_len

            # handle integer selection, i.e., single item
            if isinstance(dim_sel, int):

                dim_chunk_sel = dim_sel - dim_chunk_offset

                # N.B., leave dim_out_sel as None, as this dimension has been dropped in the
                # output array because of single value index

            # handle slice selection, i.e., contiguous range of items
            elif isinstance(dim_sel, slice):

                if dim_sel.start <= dim_chunk_offset:
                    # selection starts before current chunk
                    dim_chunk_sel_start = 0
                    dim_out_offset = dim_chunk_offset - dim_sel.start

                else:
                    # selection starts within current chunk
                    dim_chunk_sel_start = dim_sel.start - dim_chunk_offset
                    dim_out_offset = 0

                if dim_sel.stop > dim_chunk_offset + dim_chunk_len:
                    # selection ends after current chunk
                    dim_chunk_sel_stop = dim_chunk_len

                else:
                    # selection ends within current chunk
                    dim_chunk_sel_stop = dim_sel.stop - dim_chunk_offset

                dim_chunk_sel = slice(dim_chunk_sel_start, dim_chunk_sel_stop)
                dim_chunk_nitems = dim_chunk_sel_stop - dim_chunk_sel_start
                dim_out_sel = slice(dim_out_offset, dim_out_offset + dim_chunk_nitems)

            elif isinstance(dim_sel, (BoolArrayDimSelection, IntArrayDimSelection)):

                # get selection to extract data for the current chunk
                dim_chunk_sel = dim_sel.get_chunk_sel(dim_chunk_idx)

                # figure out where to put these items in the output array
                dim_out_sel = dim_sel.get_out_sel(dim_chunk_idx)

            else:
                raise RuntimeError('unexpected selection type')

            # add to chunk selection
            chunk_selection.append(dim_chunk_sel)

            # add to output selection
            if dim_out_sel is not None:
                out_selection.append(dim_out_sel)

        # normalise for indexing into numpy arrays
        chunk_selection = tuple(chunk_selection)
        out_selection = tuple(out_selection)

        # handle advanced indexing arrays orthogonally
        if self.is_advanced:
            # numpy doesn't support orthogonal indexing directly as yet, so need to work
            # around via np.ix_. Also np.ix_ does not support a mixture of arrays and slices
            # or integers, so need to convert slices and integers into ranges.
            chunk_selection = ix_(*chunk_selection)

        return chunk_selection, out_selection


# noinspection PyProtectedMember
class CoordinateIndexer(IndexerBase):

    def __init__(self, selection, array):

        # some initial normalization
        if not isinstance(selection, tuple):
            selection = tuple(selection)
        selection = replace_lists(selection)

        # validation
        if not is_coordinate_selection(selection, array):
            # TODO refactor error messages for consistency
            raise IndexError('invalid coordinate selection')

        # more normalization
        selection = self.normalize_selection(selection, array)

        # super initialisation
        super(CoordinateIndexer, self).__init__(selection, array)

        # compute flattened chunk indices for each point selected
        chunks_multi_index = tuple(
            dim_sel // dim_chunk_len
            for (dim_sel, dim_chunk_len) in zip(selection, array._chunks)
        )
        chunks_raveled_indices = np.ravel_multi_index(chunks_multi_index,
                                                      dims=array._cdata_shape)

        # validated that indices are monotonically increasing
        if np.any(np.diff(chunks_raveled_indices) < 0):
            raise NotImplementedError('only monotonically increasing indices are supported')

        # compute various useful things
        self.chunk_nitems = np.bincount(chunks_raveled_indices)
        self.chunk_nitems_cumsum = np.cumsum(self.chunk_nitems)
        self.nitems = len(chunks_raveled_indices)
        self.sel_shape = (self.nitems,)
        self.chunk_ranges = np.unravel_index(np.unique(chunks_raveled_indices),
                                             dims=array._cdata_shape)

    def normalize_selection(self, selection, array):

        # attempt to broadcast selection - this will raise error if array dimensions don't match
        selection = np.broadcast_arrays(*selection)

        for dim_sel, dim_len in zip(selection, array.shape):

            # check number of dimensions, only support indexing with 1d array
            if len(dim_sel.shape) > 1:
                raise IndexError('can only index with integer or 1-dimensional integer array')

            # handle wraparound
            loc_neg = dim_sel < 0
            if np.any(loc_neg):
                # TODO need to take a copy here, or OK to replace?
                dim_sel[loc_neg] = dim_sel[loc_neg] + dim_len

            # handle out of bounds
            if np.any(dim_sel < 0) or np.any(dim_sel >= dim_len):
                raise IndexError('index out of bounds')

        return selection

    def get_overlapping_chunks(self):
        """Convenience function to find chunks overlapping an array selection. N.B.,
        assumes selection has already been normalized."""

        return self.chunk_ranges, self.sel_shape

    def get_chunk_projection(self, chunk_coords):

        # chunk_coords: holds the index along each dimension for the current chunk within the
        # chunk grid. E.g., (0, 0) locates the first (top left) chunk in a 2D chunk grid.

        chunk_idx = np.ravel_multi_index(*chunk_coords, dims=self.array._cdata_shape)
        if chunk_idx == 0:
            out_start = 0
        else:
            out_start = self.chunk_nitems_cumsum[chunk_idx - 1]
        out_stop = self.chunk_nitems_cumsum[chunk_idx]
        out_selection = slice(out_start, out_stop)

        chunk_offsets = tuple(
            dim_chunk_idx * dim_chunk_len
            for dim_chunk_idx, dim_chunk_len in zip(chunk_coords, self.array._chunks)
        )
        chunk_selection = tuple(
            dim_sel[out_selection] - dim_chunk_offset
            for (dim_sel, dim_chunk_offset) in zip(self.selection, chunk_offsets)
        )

        return chunk_selection, out_selection


def slice_to_range(dim_sel):
    return range(dim_sel.start, dim_sel.stop, 1 if dim_sel.step is None else dim_sel.step)


def ix_(*selection):
    """Convert an orthogonal selection to a numpy advanced (fancy) selection, with support for
    slices and single ints."""

    # replace slice and int as these are not supported by numpy ix_()
    selection = [slice_to_range(dim_sel) if isinstance(dim_sel, slice)
                 else [dim_sel] if isinstance(dim_sel, int)
                 else dim_sel
                 for dim_sel in selection]

    selection = np.ix_(*selection)

    return selection


class IntArrayDimSelection(object):

    def __init__(self, dim_sel, dim_len, dim_chunk_len):

        # has to be a numpy array so we can do bincount
        dim_sel = np.asanyarray(dim_sel)

        # check number of dimensions, only support indexing with 1d array
        if len(dim_sel.shape) > 1:
            raise IndexError('can only index with 1-dimensional integer array')

        # handle wraparound
        loc_neg = dim_sel < 0
        if np.any(loc_neg):
            dim_sel[loc_neg] = dim_sel[loc_neg] + dim_len

        # handle out of bounds
        if np.any(dim_sel < 0) or np.any(dim_sel >= dim_len):
            raise IndexError('index out of bounds')

        # validate monotonically increasing
        if np.any(np.diff(dim_sel) < 0):
            raise NotImplementedError('only monotonically increasing indices are supported')

        # store attributes
        self.dim_sel = dim_sel
        self.dim_len = dim_len
        self.dim_chunk_len = dim_chunk_len
        self.nchunks = int(np.ceil(self.dim_len / self.dim_chunk_len))

        # precompute number of selected items for each chunk
        # note: for dense integer selections, the division operation here is the bottleneck
        self.chunk_nitems = np.bincount(self.dim_sel // self.dim_chunk_len, minlength=self.nchunks)
        self.chunk_nitems_cumsum = np.cumsum(self.chunk_nitems)
        self.nitems = len(dim_sel)

    def get_chunk_sel(self, dim_chunk_idx):
        # need to slice out relevant indices from the total selection, then subtract the chunk
        # offset

        dim_out_sel = self.get_out_sel(dim_chunk_idx)
        dim_chunk_offset = dim_chunk_idx * self.dim_chunk_len
        dim_chunk_sel = self.dim_sel[dim_out_sel] - dim_chunk_offset

        return dim_chunk_sel

    def get_out_sel(self, dim_chunk_idx):
        if dim_chunk_idx == 0:
            start = 0
        else:
            start = self.chunk_nitems_cumsum[dim_chunk_idx - 1]
        stop = self.chunk_nitems_cumsum[dim_chunk_idx]
        return slice(start, stop)

    def get_overlapping_chunks(self):
        return np.nonzero(self.chunk_nitems)[0]


class BoolArrayDimSelection(object):

    def __init__(self, dim_sel, dim_len, dim_chunk_len):

        # check number of dimensions, only support indexing with 1d array
        if len(dim_sel.shape) > 1:
            raise IndexError('can only index with 1-dimensional Boolean array')

        # check shape
        if dim_sel.shape[0] != dim_len:
            raise IndexError('Boolean array has wrong length; expected %s, found %s' %
                             (dim_len, dim_sel.shape[0]))

        # store attributes
        self.dim_sel = dim_sel
        self.dim_len = dim_len
        self.dim_chunk_len = dim_chunk_len
        self.nchunks = int(np.ceil(self.dim_len / self.dim_chunk_len))

        # precompute number of selected items for each chunk
        self.chunk_nitems = np.zeros(self.nchunks, dtype='i8')
        for dim_chunk_idx in range(self.nchunks):
            dim_chunk_offset = dim_chunk_idx * self.dim_chunk_len
            self.chunk_nitems[dim_chunk_idx] = np.count_nonzero(
                self.dim_sel[dim_chunk_offset:dim_chunk_offset + self.dim_chunk_len]
            )
        self.chunk_nitems_cumsum = np.cumsum(self.chunk_nitems)
        self.nitems = self.chunk_nitems_cumsum[-1]

    def get_chunk_sel(self, dim_chunk_idx):
        dim_chunk_offset = dim_chunk_idx * self.dim_chunk_len
        dim_chunk_sel = self.dim_sel[dim_chunk_offset:dim_chunk_offset + self.dim_chunk_len]
        # pad out if final chunk
        if dim_chunk_sel.shape[0] < self.dim_chunk_len:
            tmp = np.zeros(self.dim_chunk_len, dtype=bool)
            tmp[:dim_chunk_sel.shape[0]] = dim_chunk_sel
            dim_chunk_sel = tmp
        return dim_chunk_sel

    def get_out_sel(self, dim_chunk_idx):
        if dim_chunk_idx == 0:
            start = 0
        else:
            start = self.chunk_nitems_cumsum[dim_chunk_idx - 1]
        stop = self.chunk_nitems_cumsum[dim_chunk_idx]
        return slice(start, stop)

    def get_overlapping_chunks(self):
        return np.nonzero(self.chunk_nitems)[0]
