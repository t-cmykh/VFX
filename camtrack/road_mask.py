"""Segmentation de la route (asphalte gris) par image. Usage: python3 road_mask.py shot.mp4 outdir"""
import sys, os, cv2, numpy as np


def road_mask(bgr):
    bgr = cv2.GaussianBlur(bgr, (5, 5), 0)
    v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32) / 255
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    m = ((chroma < 9) & (v > 0.25) & (v < 0.75)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab_, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:  # garde la plus grosse composante
        k = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        m = (lab_ == k).astype(np.uint8)
    return m


if __name__ == "__main__":
    cap = cv2.VideoCapture(sys.argv[1])
    os.makedirs(sys.argv[2], exist_ok=True)
    i = 0
    tiles = []
    while True:
        ok, im = cap.read()
        if not ok:
            break
        i += 1
        if i in (1, 300, 600, 900, 1200):
            im = cv2.resize(im, (640, 360))
            m = road_mask(im)
            ov = im.copy()
            ov[m > 0] = (0.5 * ov[m > 0] + 0.5 * np.array([0, 0, 255])).astype(np.uint8)
            tiles.append(ov)
    tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(os.path.join(sys.argv[2], "mask_check.jpg"),
                np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:6])]))
