import threading
import time

import rclpy
from controller_manager_msgs.srv import ListControllers, SwitchController
from franka_msgs.action import Grasp, Homing, Move
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32

DEFAULT_MOVE_ACTION_TOPIC = "franka_gripper/move"
DEFAULT_GRASP_ACTION_TOPIC = "franka_gripper/grasp"
DEFAULT_HOMING_ACTION_TOPIC = "franka_gripper/homing"
DEFAULT_JOINT_STATES_TOPIC = "franka_gripper/joint_states"
DEFAULT_GRIPPER_COMMAND_TOPIC = "gripper/gripper_client/target_gripper_width_percent"
DEFAULT_MAX_GRIPPER_WIDTH = 0.08
DEFAULT_ARM_CONTROLLER = "joint_impedance_controller"


class GripperClient(Node):
    """Map GELLO Float32 percent → Franka Hand Move.

    Gripper Move while joint_impedance is ACTIVE drops the arm FCI TCP session.
    This client pauses impedance around each Move, then resumes.

    Continuous percent→width with a large deadband/interval drops brief close
    gestures and lets spring-return reopen the hand. Use open/close hysteresis
    so 合拢 latches until the GELLO axis is clearly opened again.
    """

    def __init__(self):
        super().__init__("gripper_client")
        self._cb_group = ReentrantCallbackGroup()

        self.declare_parameter("move_action_topic", DEFAULT_MOVE_ACTION_TOPIC)
        self.declare_parameter("grasp_action_topic", DEFAULT_GRASP_ACTION_TOPIC)
        self.declare_parameter("homing_action_topic", DEFAULT_HOMING_ACTION_TOPIC)
        self.declare_parameter("gripper_command_topic", DEFAULT_GRIPPER_COMMAND_TOPIC)
        self.declare_parameter("joint_states_topic", DEFAULT_JOINT_STATES_TOPIC)
        self.declare_parameter("skip_homing", True)
        self.declare_parameter("default_max_gripper_width", DEFAULT_MAX_GRIPPER_WIDTH)
        self.declare_parameter("pause_arm_for_move", True)
        self.declare_parameter("arm_controller_name", DEFAULT_ARM_CONTROLLER)
        # Legacy continuous-mode knobs (used only if binary_open_close=false).
        self.declare_parameter("command_deadband_percent", 0.05)
        self.declare_parameter("min_command_interval_sec", 1.0)
        self.declare_parameter("move_speed", 0.1)
        self.declare_parameter("switch_timeout_sec", 5.0)
        self.declare_parameter("startup_ignore_sec", 5.0)
        # Binary open/close with hysteresis (recommended for teleop + pause_arm).
        self.declare_parameter("binary_open_close", True)
        # Resting GELLO handle is ~1.0; a moderate squeeze must fully 合拢.
        # Narrow release band avoids spring jitter flipping open/close.
        self.declare_parameter("close_threshold_percent", 0.75)
        self.declare_parameter("open_threshold_percent", 0.88)
        self.declare_parameter("closed_width_m", 0.0)
        # Move(near-zero) often fails on Franka Hand; Grasp closes with force.
        self.declare_parameter("use_grasp_for_close", True)
        self.declare_parameter("grasp_force_n", 40.0)
        # Keep epsilon tight: large outer epsilon stops closing early (~0.06m).
        self.declare_parameter("grasp_epsilon_inner_m", 0.005)
        self.declare_parameter("grasp_epsilon_outer_m", 0.005)
        self.declare_parameter("invert_percent", False)
        self.declare_parameter("verify_interval_sec", 0.5)
        # Consider "open enough" only near max width; below this, re-command open.
        self.declare_parameter("open_width_min_m", 0.072)

        move_action_topic = self.get_parameter("move_action_topic").value
        grasp_action_topic = self.get_parameter("grasp_action_topic").value
        homing_action_topic = self.get_parameter("homing_action_topic").value
        gripper_command_topic = self.get_parameter("gripper_command_topic").value
        joint_states_topic = self.get_parameter("joint_states_topic").value
        skip_homing = bool(self.get_parameter("skip_homing").value)
        default_max_gripper_width = float(self.get_parameter("default_max_gripper_width").value)
        self._pause_arm_for_move = bool(self.get_parameter("pause_arm_for_move").value)
        self._arm_controller_name = str(self.get_parameter("arm_controller_name").value)
        self._command_deadband_percent = float(self.get_parameter("command_deadband_percent").value)
        self._min_command_interval_sec = float(self.get_parameter("min_command_interval_sec").value)
        self._move_speed = float(self.get_parameter("move_speed").value)
        self._switch_timeout_sec = float(self.get_parameter("switch_timeout_sec").value)
        self._startup_ignore_sec = float(self.get_parameter("startup_ignore_sec").value)
        self._binary_open_close = bool(self.get_parameter("binary_open_close").value)
        self._close_threshold = float(self.get_parameter("close_threshold_percent").value)
        self._open_threshold = float(self.get_parameter("open_threshold_percent").value)
        self._closed_width_m = float(self.get_parameter("closed_width_m").value)
        self._use_grasp_for_close = bool(self.get_parameter("use_grasp_for_close").value)
        self._grasp_force_n = float(self.get_parameter("grasp_force_n").value)
        self._grasp_epsilon_inner_m = float(self.get_parameter("grasp_epsilon_inner_m").value)
        self._grasp_epsilon_outer_m = float(self.get_parameter("grasp_epsilon_outer_m").value)
        self._invert_percent = bool(self.get_parameter("invert_percent").value)
        self._verify_interval_sec = float(self.get_parameter("verify_interval_sec").value)
        self._open_width_min_m = float(self.get_parameter("open_width_min_m").value)

        self._ACTION_SERVER_TIMEOUT = 10.0
        self._max_width = 0.0
        self._pending_percent = None
        self._last_sent_percent = None
        self._last_sent_time = 0.0
        self._latched_mode = None  # "open" | "close" | None
        self._last_width_m: float | None = None
        self._last_verify_time = 0.0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ready_at = time.monotonic() + self._startup_ignore_sec

        self.get_logger().info("Initializing gripper client...")
        if skip_homing:
            self._max_width = float(default_max_gripper_width)
            self.get_logger().info(
                f"Skipping gripper homing (skip_homing=true); "
                f"using default_max_gripper_width={self._max_width:.4f} m"
            )
        else:
            self._home_gripper(homing_action_topic)
            self._get_max_gripper_width(joint_states_topic)

        self._switch_client = self.create_client(
            SwitchController,
            "/controller_manager/switch_controller",
            callback_group=self._cb_group,
        )
        self._list_client = self.create_client(
            ListControllers,
            "/controller_manager/list_controllers",
            callback_group=self._cb_group,
        )
        if self._pause_arm_for_move:
            self.get_logger().info(
                "pause_arm_for_move=true: will deactivate "
                f"'{self._arm_controller_name}' around each Gripper Move"
            )
        if self._binary_open_close:
            self.get_logger().info(
                f"binary_open_close=true: close<= {self._close_threshold:.2f} "
                f"open>= {self._open_threshold:.2f} invert={self._invert_percent} "
                f"grasp_close={self._use_grasp_for_close} open_min={self._open_width_min_m:.3f}m"
            )

        self.create_subscription(
            Float32,
            gripper_command_topic,
            self._gripper_command_callback,
            10,
            callback_group=self._cb_group,
        )
        self.create_subscription(
            JointState,
            joint_states_topic,
            self._joint_state_callback,
            10,
            callback_group=self._cb_group,
        )
        self._action_client = ActionClient(
            self, Move, move_action_topic, callback_group=self._cb_group
        )
        self._grasp_client = ActionClient(
            self, Grasp, grasp_action_topic, callback_group=self._cb_group
        )

        self.get_logger().info("Waiting for gripper move action server...")
        if not self._action_client.wait_for_server(timeout_sec=self._ACTION_SERVER_TIMEOUT):
            raise RuntimeError(
                f"Move action server not available after {self._ACTION_SERVER_TIMEOUT} seconds!"
            )
        if self._use_grasp_for_close:
            self.get_logger().info("Waiting for gripper grasp action server...")
            if not self._grasp_client.wait_for_server(timeout_sec=self._ACTION_SERVER_TIMEOUT):
                raise RuntimeError(
                    f"Grasp action server not available after {self._ACTION_SERVER_TIMEOUT} seconds!"
                )

        self._worker = threading.Thread(target=self._worker_loop, name="gripper_move_worker", daemon=True)
        self._worker.start()
        self.get_logger().info(
            f"Gripper client initialized (ignore GELLO cmds for {self._startup_ignore_sec:.1f}s)!"
        )

    def destroy_node(self):
        self._stop.set()
        self._wake.set()
        if hasattr(self, "_worker") and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        super().destroy_node()

    def _home_gripper(self, homing_action_topic: str) -> None:
        self.get_logger().info("Starting gripper homing...")
        homing_client = ActionClient(self, Homing, homing_action_topic)
        if not homing_client.wait_for_server(timeout_sec=self._ACTION_SERVER_TIMEOUT):
            raise RuntimeError("Homing action server not available!")
        goal_msg = Homing.Goal()
        future = homing_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, future)
        goal_handle = future.result()
        if not goal_handle.accepted:
            raise RuntimeError("Homing action rejected!")
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        result = result_future.result()
        time.sleep(2)
        if not result.result.success:
            raise RuntimeError("Gripper homing failed!")
        self.get_logger().info("Gripper homing successful!")

    def _get_max_gripper_width(self, joint_states_topic: str) -> None:
        future = rclpy.task.Future()

        def joint_state_callback(msg):
            self._max_width = 2 * msg.position[0]
            self.get_logger().info(f"Maximum gripper width determined: {self._max_width}")
            if not future.done():
                future.set_result(True)

        sub = self.create_subscription(JointState, joint_states_topic, joint_state_callback, 10)
        rclpy.spin_until_future_complete(self, future, timeout_sec=self._ACTION_SERVER_TIMEOUT)
        self.destroy_subscription(sub)
        if self._max_width <= 0.0:
            raise RuntimeError("Failed to read max gripper width")

    def _normalize_percent(self, raw: float) -> float:
        percent = max(0.0, min(1.0, float(raw)))
        if self._invert_percent:
            percent = 1.0 - percent
        return percent

    def _gripper_command_callback(self, msg: Float32) -> None:
        percent = self._normalize_percent(float(msg.data))
        with self._lock:
            self._pending_percent = percent
        self._wake.set()

    def _joint_state_callback(self, msg: JointState) -> None:
        if not msg.position:
            return
        # Franka Hand: width ≈ sum of both finger positions.
        width = float(sum(msg.position[:2])) if len(msg.position) >= 2 else float(msg.position[0]) * 2.0
        with self._lock:
            self._last_width_m = width
        self._wake.set()

    def _wait_future(self, future, timeout_sec: float):
        deadline = time.monotonic() + timeout_sec
        while not future.done() and time.monotonic() < deadline and rclpy.ok():
            time.sleep(0.02)
        return future.done()

    def _controller_state(self) -> str | None:
        """Return 'active' / 'inactive' / ... or None if unknown."""
        if not self._list_client.wait_for_service(timeout_sec=self._switch_timeout_sec):
            return None
        future = self._list_client.call_async(ListControllers.Request())
        if not self._wait_future(future, self._switch_timeout_sec + 1.0):
            return None
        try:
            resp = future.result()
        except Exception:  # noqa: BLE001
            return None
        for ctrl in resp.controller:
            if ctrl.name == self._arm_controller_name:
                return ctrl.state
        return None

    def _call_switch(self, *, activate: bool) -> bool:
        desired = "active" if activate else "inactive"
        for attempt in range(1, 4):
            state = self._controller_state()
            if state == desired:
                return True
            if not self._switch_client.wait_for_service(timeout_sec=self._switch_timeout_sec):
                self.get_logger().error("switch_controller service unavailable")
                return False
            req = SwitchController.Request()
            if activate:
                req.activate_controllers = [self._arm_controller_name]
                req.deactivate_controllers = []
            else:
                req.activate_controllers = []
                req.deactivate_controllers = [self._arm_controller_name]
            req.strictness = SwitchController.Request.BEST_EFFORT
            req.activate_asap = True
            req.timeout.sec = int(self._switch_timeout_sec)
            req.timeout.nanosec = int((self._switch_timeout_sec % 1) * 1e9)

            future = self._switch_client.call_async(req)
            if not self._wait_future(future, self._switch_timeout_sec + 1.0):
                self.get_logger().warn(
                    f"switch_controller timed out (attempt {attempt}/3); checking state"
                )
                time.sleep(0.2)
                if self._controller_state() == desired:
                    return True
                continue
            try:
                resp = future.result()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"switch_controller failed (attempt {attempt}/3): {exc}")
                time.sleep(0.2)
                if self._controller_state() == desired:
                    return True
                continue
            # Give controller_manager a moment; list_controllers is often slow under load.
            time.sleep(0.15)
            state_after = self._controller_state()
            if state_after == desired:
                return True
            if not resp.ok:
                self.get_logger().warn(
                    f"switch_controller ok=false (attempt {attempt}/3 activate={activate} "
                    f"controller={self._arm_controller_name} state={state_after})"
                )
            else:
                self.get_logger().warn(
                    f"switch_controller ok=true but state still {state_after} "
                    f"(attempt {attempt}/3 activate={activate})"
                )
            time.sleep(0.25)
        return self._controller_state() == desired

    def _execute_gripper_goal(self, *, kind: str, width_m: float) -> bool:
        """Run Move or Grasp with optional arm pause around the action."""
        paused = False
        ok = False
        try:
            if self._pause_arm_for_move:
                if not self._call_switch(activate=False):
                    # Prefer attempting gripper motion over blocking open/close forever.
                    # FCI may drop; arm stack watchdog / recover can restore if needed.
                    self.get_logger().warn(
                        f"{kind}: failed to pause arm controller — proceeding anyway"
                    )
                    paused = False
                else:
                    paused = True
                    time.sleep(0.25)

            if kind == "grasp":
                goal_msg = Grasp.Goal()
                goal_msg.width = float(width_m)
                goal_msg.speed = float(self._move_speed)
                goal_msg.force = float(self._grasp_force_n)
                goal_msg.epsilon.inner = float(self._grasp_epsilon_inner_m)
                goal_msg.epsilon.outer = float(self._grasp_epsilon_outer_m)
                client = self._grasp_client
                label = (
                    f"Gripper Grasp width={width_m:.4f} m speed={self._move_speed:.3f} "
                    f"force={self._grasp_force_n:.1f}N (arm_paused={paused})"
                )
            else:
                goal_msg = Move.Goal()
                goal_msg.width = float(width_m)
                goal_msg.speed = float(self._move_speed)
                client = self._action_client
                label = (
                    f"Gripper Move width={width_m:.4f} m speed={self._move_speed:.3f} "
                    f"(arm_paused={paused})"
                )

            self.get_logger().info(label)
            send_future = client.send_goal_async(goal_msg)
            deadline = time.monotonic() + self._ACTION_SERVER_TIMEOUT
            while not send_future.done() and time.monotonic() < deadline and rclpy.ok():
                time.sleep(0.02)
            if not send_future.done():
                self.get_logger().error(f"{kind} send_goal timed out")
                return False
            goal_handle = send_future.result()
            if not goal_handle.accepted:
                self.get_logger().error(f"{kind} goal rejected: {goal_handle.status}")
                return False
            result_future = goal_handle.get_result_async()
            deadline = time.monotonic() + 15.0
            while not result_future.done() and time.monotonic() < deadline and rclpy.ok():
                time.sleep(0.05)
            if not result_future.done():
                self.get_logger().error(f"{kind} result timed out")
                return False
            result = result_future.result().result
            self.get_logger().info(f"{kind} result: {result}")
            ok = bool(getattr(result, "success", False))
            # Grasp may return success=False when no object is held; fingers may still
            # be closed. Treat near-closed width as success for teleop latching.
            if kind == "grasp" and not ok:
                with self._lock:
                    width = self._last_width_m
                if width is not None and width <= 0.02:
                    self.get_logger().warn(
                        f"Grasp reported failure but width={width:.4f}m closed — treating as ok"
                    )
                    ok = True
            return ok
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"{kind} failed: {exc}")
            return False
        finally:
            if paused:
                if not self._call_switch(activate=True):
                    self.get_logger().error(
                        f"Failed to re-activate arm controller after Gripper {kind} — retrying"
                    )
                    time.sleep(0.3)
                    self._call_switch(activate=True)

    def _execute_move(self, width_m: float) -> bool:
        return self._execute_gripper_goal(kind="move", width_m=width_m)

    def _execute_close(self) -> bool:
        if self._use_grasp_for_close:
            return self._execute_gripper_goal(kind="grasp", width_m=self._closed_width_m)
        return self._execute_move(self._closed_width_m)

    def _execute_open(self) -> bool:
        return self._execute_move(self._max_width)

    def _desired_mode(self, percent: float) -> str | None:
        """Hysteresis: close below low threshold, open above high; else keep latch."""
        if percent <= self._close_threshold:
            return "close"
        if percent >= self._open_threshold:
            return "open"
        return self._latched_mode

    def _measured_mode(self) -> str | None:
        with self._lock:
            width = self._last_width_m
        if width is None:
            return None
        if width >= self._open_width_min_m:
            return "open"
        if width <= 0.02:
            return "close"
        return None

    def _needs_recommand(self, mode: str) -> bool:
        """True when fingers disagree with desired open/close."""
        with self._lock:
            width = self._last_width_m
        if width is None:
            return False
        if mode == "open" and width < self._open_width_min_m:
            self.get_logger().warn(
                f"GELLO release/open but fingers not fully open "
                f"(width={width:.4f} < {self._open_width_min_m:.4f}) — re-open"
            )
            return True
        if mode == "close" and width > 0.02:
            self.get_logger().warn(
                f"GELLO close but fingers open (width={width:.4f}) — re-close"
            )
            return True
        return False

    def _worker_loop(self) -> None:
        while not self._stop.is_set() and rclpy.ok():
            self._wake.wait(timeout=0.25)
            self._wake.clear()
            if self._stop.is_set():
                break
            if time.monotonic() < self._ready_at:
                continue

            with self._lock:
                percent = self._pending_percent
            if percent is None:
                continue

            now = time.monotonic()
            if self._binary_open_close:
                mode = self._desired_mode(percent)
                if mode is None:
                    # First command: infer from current percent.
                    mode = "close" if percent < 0.5 else "open"

                need_verify = False
                if (now - self._last_verify_time) >= self._verify_interval_sec:
                    self._last_verify_time = now
                    need_verify = self._needs_recommand(mode)

                if mode == self._latched_mode and not need_verify:
                    continue
                if (now - self._last_sent_time) < self._min_command_interval_sec and not need_verify:
                    continue

                self.get_logger().info(
                    f"Gripper mode {self._latched_mode} -> {mode} "
                    f"(gello_percent={percent:.3f} verify={need_verify})"
                )
                self._last_sent_percent = percent
                self._last_sent_time = now
                ok = self._execute_close() if mode == "close" else self._execute_open()
                if ok:
                    self._latched_mode = mode
                else:
                    # Do not latch on failure — allow retry on next cycle.
                    self.get_logger().warn(
                        f"Gripper {mode} failed; keeping latch={self._latched_mode}"
                    )
                continue

            if (now - self._last_sent_time) < self._min_command_interval_sec:
                continue

            if self._last_sent_percent is not None:
                if abs(percent - self._last_sent_percent) < self._command_deadband_percent:
                    continue

            width_m = self._max_width * percent
            self._last_sent_percent = percent
            self._last_sent_time = now
            self._execute_move(width_m)


def main(args=None):
    rclpy.init(args=args)
    node = GripperClient()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
