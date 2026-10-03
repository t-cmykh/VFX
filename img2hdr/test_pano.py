"""Tests de la geometrie de pano_outpaint (sans modele). python test_pano.py"""
import numpy as np

import pano_outpaint as po


def _known(h=128, w=256):
    k = np.zeros((h, w), bool)
    k[40:90, 100:160] = True
    return k


def test_rings_partition_unknown():
    k = _known()
    ms = po.ring_masks(k, 4, overlap_px=0)
    cover = np.zeros_like(k)
    for m in ms:
        assert not (cover & m).any()            # anneaux disjoints sans recouvrement
        cover |= m
    assert (cover == ~k).all()                  # ils couvrent exactement l'inconnu
    areas = [m.sum() for m in ms]
    assert max(areas) / min(areas) < 1.3        # aires a peu pres egales


def test_rings_ordered_by_distance():
    k = _known()
    d = po.wrapped_distance(~k)
    ms = po.ring_masks(k, 3, overlap_px=0)
    means = [d[m].mean() for m in ms]
    assert means == sorted(means)


def test_wrap_distance_is_periodic():
    k = np.zeros((64, 128), bool)
    k[:, 0:4] = True                            # connu sur le bord gauche
    d = po.wrapped_distance(~k)
    assert d[32, 127] < 3 and d[32, 120] > d[32, 127]   # le bord droit est PROCHE (wrap)


def test_overlap_extends_into_known():
    k = _known()
    m0 = po.ring_masks(k, 2, 0)[0]
    m1 = po.ring_masks(k, 2, 4)[0]
    assert (m1 & k).any() and not (m0 & k).any()


def test_seam_mask_is_centered():
    m = po.seam_mask((64, 200), 0.2)
    cols = np.where(m.any(axis=0))[0]
    assert cols.min() == 80 and cols.max() == 119 and m.all(axis=0)[cols].all()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
