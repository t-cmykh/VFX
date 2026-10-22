"""Tests hors Maya : injection dans les workflows, historique, client HTTP contre un faux ComfyUI."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import comfy_client  # noqa: E402
import comfy_history  # noqa: E402
import comfy_workflow as cwf  # noqa: E402

QWEN = os.path.join(os.path.dirname(HERE), "workflows", "qwen_image_api.json")

LTX_LIKE = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}, "_meta": {"title": "MAYA_KEY_1"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "b.png"}, "_meta": {"title": "MAYA_KEY_2"}},
    "3": {"class_type": "Guide", "inputs": {"image": ["1", 0], "frame_idx": 0}, "_meta": {"title": "MAYA_KEY_1_IDX"}},
    "4": {"class_type": "Guide", "inputs": {"image": ["2", 0], "frame_idx": -1}, "_meta": {"title": "MAYA_KEY_2_IDX"}},
    "5": {"class_type": "EmptyLatent", "inputs": {"width": 768, "height": 512, "length": 97},
          "_meta": {"title": "MAYA_SIZE, MAYA_FRAMES"}},
    "6": {"class_type": "PrimitiveFloat", "inputs": {"value": 24.0}, "_meta": {"title": "MAYA_FPS"}},
}


class WorkflowTests(unittest.TestCase):
    def test_example_qwen_workflow(self):
        wf = cwf.load_api_workflow(QWEN)
        out, missing = cwf.inject(wf, {"MAYA_PROMPT": "chateau", "MAYA_NEGATIVE": "flou", "MAYA_SEED": 42,
                                       "MAYA_DENOISE": 0.5, "MAYA_SIZE": (1024, 432),
                                       "MAYA_IMAGE": "maya_comfy/img.0010.png", "MAYA_DEPTH": "x.png"})
        self.assertEqual(missing, ["MAYA_DEPTH"])
        self.assertEqual(out["6"]["inputs"]["text"], "chateau")
        self.assertEqual(out["7"]["inputs"]["text"], "flou")
        self.assertEqual(out["11"]["inputs"]["seed"], 42)
        self.assertEqual(out["11"]["inputs"]["denoise"], 0.5)
        self.assertEqual((out["9"]["inputs"]["width"], out["9"]["inputs"]["height"]), (1024, 432))
        self.assertEqual(out["11"]["inputs"]["model"], ["3", 0])     # liens intacts
        self.assertEqual(wf["11"]["inputs"]["seed"], 0)                # original non modifie

    def test_keys_frames_fps(self):
        self.assertEqual(cwf.key_slots(LTX_LIKE), 2)
        out, missing = cwf.inject(LTX_LIKE, {"MAYA_KEY_1": "k1.png", "MAYA_KEY_1_IDX": 0, "MAYA_KEY_2": "k2.png",
                                             "MAYA_KEY_2_IDX": 40, "MAYA_SIZE": (512, 288), "MAYA_FRAMES": 49,
                                             "MAYA_FPS": 25.0})
        self.assertEqual(missing, [])
        self.assertEqual(out["3"]["inputs"], {"image": ["1", 0], "frame_idx": 0})
        self.assertEqual(out["4"]["inputs"]["frame_idx"], 40)
        self.assertEqual(out["5"]["inputs"], {"width": 512, "height": 288, "length": 49})
        self.assertEqual(out["6"]["inputs"]["value"], 25.0)

    def test_linked_input_is_an_error(self):
        wf = {"1": {"class_type": "KSampler", "inputs": {"seed": ["9", 0]}, "_meta": {"title": "MAYA_SEED"}}}
        with self.assertRaises(cwf.WorkflowError):
            cwf.inject(wf, {"MAYA_SEED": 1})

    def test_ui_format_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"nodes": [], "links": []}, f)
        with self.assertRaisesRegex(cwf.WorkflowError, "Export"):
            cwf.load_api_workflow(f.name)
        os.remove(f.name)

    def test_bad_key_numbering(self):
        wf = {"1": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "MAYA_KEY_2"}}}
        with self.assertRaises(cwf.WorkflowError):
            cwf.key_slots(wf)

    def test_assign_keys(self):
        self.assertEqual(cwf.assign_keys([1, 2], 4), [1, 2, 2, 2])
        self.assertEqual(cwf.assign_keys([1, 2, 3, 4, 5], 3), [1, 3, 5])
        self.assertEqual(cwf.assign_keys([1, 2, 3], 1), [1])
        self.assertEqual(cwf.assign_keys([], 2), [])

    def test_snap(self):
        self.assertEqual(cwf.snap_frames(100), 97)
        self.assertEqual(cwf.snap_frames(97), 97)
        self.assertEqual(cwf.snap_frames(100, max_frames=49), 49)
        self.assertEqual(cwf.snap_frames(5), 9)
        self.assertEqual(cwf.snap_frames(81, step=4), 81)
        self.assertEqual(cwf.size_from_aspect(1920, 1080, 512, 32), (512, 288))
        self.assertEqual(cwf.size_from_aspect(1080, 1920, 1024, 16), (576, 1024))


class HistoryTests(unittest.TestCase):
    def test_add_select_remove(self):
        root = tempfile.mkdtemp()
        h = comfy_history.History(os.path.join(root, "hist"))
        srcs = []
        for i in range(3):
            p = os.path.join(root, "src%d.png" % i)
            open(p, "wb").write(b"png")
            srcs.append(p)
        a = h.add(srcs[0], {"frame": 30, "seed": 1})
        b = h.add(srcs[1], {"frame": 10, "seed": 2})
        c = h.add(srcs[2], {"frame": 20, "seed": 3})
        self.assertEqual(len({a["path"], b["path"], c["path"]}), 3)
        self.assertEqual(len(h.entries()), 3)
        h.update(a["path"], selected=True)
        h.update(b["path"], selected=True)
        self.assertEqual([e["frame"] for e in h.selected()], [10, 30])
        h.remove(b["path"])
        self.assertEqual([e["frame"] for e in h.selected()], [30])
        self.assertEqual(len(h.entries()), 2)


class _FakeComfy(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, raw=None):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        st = self.state
        if url.path == "/system_stats":
            return self._send({"devices": [{"name": "fake", "vram_free": 2 ** 30}]})
        if url.path == "/queue":
            return self._send({"queue_running": [[0, "p1"]], "queue_pending": []})
        if url.path.startswith("/history/"):
            st["polls"] = st.get("polls", 0) + 1
            if st["polls"] < 2:
                return self._send({})
            if st.get("fail"):
                return self._send({"p1": {"status": {"status_str": "error", "messages": [
                    ["execution_error", {"node_type": "KSampler", "node_id": "11",
                                         "exception_message": "CUDA out of memory"}]]}, "outputs": {}}})
            return self._send({"p1": {"status": {"status_str": "success"}, "outputs": {
                "13": {"images": [{"filename": "qwen_00001_.png", "subfolder": "maya_comfy", "type": "output"}]},
                "14": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]}}}})
        if url.path == "/view":
            q = parse_qs(url.query)
            return self._send(None, raw=("IMG:%s/%s" % (q["subfolder"][0], q["filename"][0])).encode())
        self._send({}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        st = self.state
        if self.path == "/upload/image":
            st["upload"] = body
            return self._send({"name": "img.0010.png", "subfolder": "maya_comfy", "type": "input"})
        if self.path == "/prompt":
            wf = json.loads(body)["prompt"]
            if wf.get("bad"):
                return self._send({"error": {"message": "Prompt outputs failed validation", "details": ""},
                                   "node_errors": {"8": {"class_type": "LoadImage", "errors": [
                                       {"message": "Invalid image file", "details": "image: x.png"}]}}}, 400)
            st["prompt"] = wf
            return self._send({"prompt_id": "p1", "number": 0, "node_errors": {}})
        if self.path in ("/free", "/interrupt", "/queue"):
            st.setdefault("calls", []).append(self.path)
            return self._send(None, raw=b"")
        self._send({}, 404)


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeComfy)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addr = "127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _FakeComfy.state.clear()
        self.client = comfy_client.ComfyClient(self.addr)

    def test_full_round_trip(self):
        self.assertEqual(self.client.ping()["devices"][0]["name"], "fake")
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "img.0010.png")
        open(src, "wb").write(b"\x89PNGdata")
        ref = self.client.upload(src)
        self.assertEqual(ref, "maya_comfy/img.0010.png")
        self.assertIn(b'filename="img.0010.png"', _FakeComfy.state["upload"])
        self.assertIn(b"\x89PNGdata", _FakeComfy.state["upload"])
        wf, _ = cwf.inject(cwf.load_api_workflow(QWEN), {"MAYA_IMAGE": ref})
        pid = self.client.queue(wf)
        self.assertEqual(_FakeComfy.state["prompt"]["8"]["inputs"]["image"], ref)
        progress = []
        outputs = self.client.wait(pid, poll=0.01, on_progress=progress.append)
        self.assertTrue(progress and "en cours" in progress[0])
        files = comfy_client.output_files(outputs)
        self.assertEqual([f["filename"] for f in files], ["qwen_00001_.png"])    # apercu temp ignore
        path = self.client.download(files[0], tmp)
        self.assertEqual(open(path, "rb").read(), b"IMG:maya_comfy/qwen_00001_.png")
        self.client.free()
        self.assertEqual(_FakeComfy.state["calls"], ["/free"])

    def test_validation_error_is_readable(self):
        with self.assertRaisesRegex(comfy_client.ComfyError, "Invalid image file"):
            self.client.queue({"bad": True})

    def test_execution_error(self):
        _FakeComfy.state["fail"] = True
        with self.assertRaisesRegex(comfy_client.ComfyError, "out of memory"):
            self.client.wait("p1", poll=0.01)

    def test_cancel(self):
        with self.assertRaises(comfy_client.ComfyCancelled):
            self.client.wait("p1", poll=0.01, should_stop=lambda: True)
        self.assertEqual(_FakeComfy.state["calls"], ["/queue", "/interrupt"])

    def test_unreachable(self):
        with self.assertRaisesRegex(comfy_client.ComfyError, "injoignable"):
            comfy_client.ComfyClient("127.0.0.1:1").ping()


if __name__ == "__main__":
    unittest.main()
