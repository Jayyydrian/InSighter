from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace

from filesystem_monitor import FileActivityHandler, FilesystemMonitor
from usb_monitor import discover_removable_volumes, volume_changes


class UsbMonitorTests(unittest.TestCase):
    def test_discovers_only_removable_mounted_volumes(self):
        partitions = [
            SimpleNamespace(mountpoint="E:\\"),
            SimpleNamespace(mountpoint="C:\\"),
            SimpleNamespace(mountpoint="F:\\"),
        ]

        volumes = discover_removable_volumes(
            partitions, is_removable=lambda root: root in {"E:\\", "F:\\"}
        )

        self.assertEqual(volumes, ["E:\\", "F:\\"])

    def test_reports_initial_connection_and_later_connection_or_removal(self):
        self.assertEqual(
            volume_changes(set(), {"E:\\"}),
            [{"event_type": "usb_connected", "drive_letter": "E:\\"}],
        )
        self.assertEqual(
            volume_changes({"E:\\"}, {"F:\\"}),
            [
                {"event_type": "usb_connected", "drive_letter": "F:\\"},
                {"event_type": "usb_disconnected", "drive_letter": "E:\\"},
            ],
        )


class FilesystemMonitorTests(unittest.TestCase):
    def test_counts_selected_file_operations_without_retaining_paths(self):
        handler = FileActivityHandler("E:\\")
        for operation in ("created", "modified", "deleted", "moved", "opened"):
            handler.dispatch(SimpleNamespace(
                event_type=operation,
                is_directory=False,
                src_path="E:\\private\\secret.txt",
                dest_path="E:\\private\\renamed.txt",
            ))
        handler.dispatch(SimpleNamespace(
            event_type="created",
            is_directory=True,
            src_path="E:\\private",
        ))

        counts = handler.drain()

        self.assertEqual(
            counts,
            {"create": 1, "modify": 1, "delete": 1, "rename": 1},
        )
        self.assertNotIn("secret.txt", repr(counts))
        self.assertEqual(handler.drain(), {})

    def test_watchdog_observer_counts_real_create_modify_rename_delete(self):
        monitor = FilesystemMonitor()
        observed = {}

        def wait_for(operation):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for event in monitor.drain_events():
                    for name, count in event["operation_counts"].items():
                        observed[name] = observed.get(name, 0) + count
                if observed.get(operation, 0):
                    return
                time.sleep(0.05)
            self.fail(f"Timed out waiting for {operation} event.")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-name.txt"
            renamed = Path(directory) / "renamed-private-name.txt"
            try:
                monitor.start()
                monitor.sync_volumes({directory})
                path.write_text("first", encoding="utf-8")
                wait_for("create")
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(" update")
                wait_for("modify")
                path.rename(renamed)
                wait_for("rename")
                renamed.unlink()
                wait_for("delete")
            finally:
                monitor.stop()

        self.assertEqual(
            set(observed), {"create", "modify", "rename", "delete"}
        )


if __name__ == "__main__":
    unittest.main()
