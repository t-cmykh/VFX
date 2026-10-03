"""python -m pytest test_colorspace.py  (ou: python test_colorspace.py)"""
import numpy as np

import colorspace as cs

REF = np.array([[0.6131324224, 0.3395380158, 0.0474166960],     # sRGB -> ACEScg, valeurs publiees
                [0.0701243808, 0.9163940113, 0.0134515240],
                [0.0205876575, 0.1095745716, 0.8697854040]])


def test_matrix_matches_published():
    assert np.abs(cs.SRGB_TO_ACESCG - REF).max() < 1e-3


def test_white_is_preserved():
    assert np.allclose(cs.SRGB_TO_ACESCG @ np.ones(3), 1.0, atol=1e-6)


def test_roundtrip_with_hdr_values():
    x = (np.random.default_rng(0).random((8, 8, 3)) * 50).astype(np.float32)
    assert np.allclose(cs.acescg_to_srgb(cs.srgb_to_acescg(x)), x, rtol=1e-4, atol=1e-4)


def test_srgb_decode_endpoints():
    assert cs.srgb_decode(np.array([0.0, 1.0]))[1] == 1.0 and cs.srgb_decode(np.array([0.0]))[0] == 0.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
