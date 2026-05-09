import json
import os
import random
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.all import EventMessageType, event_message_type
from astrbot.api import logger

RNG = random.SystemRandom()
DEFAULT_A = 10
DEFAULT_K = 8
DEFAULT_M = 10


@register("astrbot_plugin_ww_dice", "pAzoth", "WW无限规则骰池系统", "1.0.0")
class WWDicePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.wakeup_prefix = [".", "。"]
        self.data_dir = StarTools.get_data_dir()
        self.config_path = os.path.join(str(self.data_dir), "ww_defaults.json")
        self.switch_path = os.path.join(str(self.data_dir), "ww_switch.json")

    # ── 配置读写 ──

    def _load_defaults(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"a": DEFAULT_A, "k": DEFAULT_K, "m": DEFAULT_M}

    def _save_defaults(self, data: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 群开关 ──

    def _load_switch(self) -> dict:
        if os.path.exists(self.switch_path):
            try:
                with open(self.switch_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_switch(self, data: dict):
        with open(self.switch_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _is_disabled(self, group_id: str) -> bool:
        switch = self._load_switch()
        return switch.get(str(group_id), False)

    # ── 核心骰点 ──

    def _ww_roll(self, n: int, a: int, k: int, m: int) -> dict:
        pools, successes, pending, rnd = [], 0, n, 0
        ones, maxes = 0, 0
        while pending > 0 and rnd < 20:
            results, new_pending = [], 0
            for _ in range(pending):
                val = RNG.randint(1, m)
                triggers, is_success = val >= a, val >= k
                results.append({"v": val, "t": triggers, "s": is_success})
                if triggers: new_pending += 1
                if is_success: successes += 1
                if val == 1: ones += 1
                if val == m: maxes += 1
            pools.append(results)
            pending, rnd = new_pending, rnd + 1
        total = sum(len(p) for p in pools)
        extra_rounds = len(pools) - 1
        return {
            "pools": pools, "successes": successes, "total_dice": total,
            "ones": ones, "maxes": maxes, "extra_rounds": extra_rounds,
        }

    # ── 路由 ──

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def router(self, event: AstrMessageEvent):
        msg = event.message_obj.message_str.strip()
        if not any(msg.startswith(p) for p in self.wakeup_prefix):
            return

        body = msg[1:]
        m = re.match(r'^(ww)', body, re.I)
        if not m:
            return
        expr = body[len(m.group(1)):].strip()
        group_id = str(event.get_group_id())

        # ── on / off 开关 ──
        if expr.lower() in ("on", "off"):
            switch = self._load_switch()
            if expr.lower() == "off":
                switch[group_id] = True
                self._save_switch(switch)
                yield event.plain_result("WW骰点已关闭。")
            else:
                switch.pop(group_id, None)
                self._save_switch(switch)
                yield event.plain_result("WW骰点已开启。")
            return

        # ── 检查该群是否禁用 ──
        if self._is_disabled(group_id):
            return

        defaults = self._load_defaults()

        if not expr:
            result = self._ww_roll(10, defaults["a"], defaults["k"], defaults["m"])
            text = self._fmt(result, 10, defaults["a"], defaults["k"], defaults["m"])
            yield event.plain_result(text)
            return

        if expr.lower().startswith("set"):
            text = self._handle_set(expr, defaults)
            yield event.plain_result(text)
            return

        try:
            parsed = self._parse_expr(expr, defaults)
        except ValueError as e:
            yield event.plain_result(f"解析错误: {e}\n用法: .ww 10a5k6m7 / .ww set k6 / .ww set clr")
            return

        result = self._ww_roll(parsed["n"], parsed["a"], parsed["k"], parsed["m"])
        text = self._fmt(result, parsed["n"], parsed["a"], parsed["k"], parsed["m"])
        yield event.plain_result(text)

    # ── 解析 ──

    def _parse_expr(self, expr: str, defaults: dict) -> dict:
        expr = expr.strip().lower()
        a, k, m = defaults["a"], defaults["k"], defaults["m"]
        remaining = expr

        for key in ["m", "k", "a"]:
            match = re.search(rf"{key}(\d+)", remaining)
            if match:
                val = int(match.group(1))
                if key == "m": m = val
                elif key == "k": k = val
                elif key == "a": a = val
                remaining = remaining.replace(match.group(0), "")

        remaining = remaining.strip()
        if not remaining.isdigit():
            raise ValueError(f"无法解析表达式，剩余: '{remaining}'")
        n = int(remaining)

        if not (1 <= n <= 500):
            raise ValueError(f"骰子数量需 1-500，收到 {n}")
        if m < 2:
            raise ValueError("面数至少为 2")
        if a < 2 or a > m:
            raise ValueError(f"加骰线需 2-{m}，收到 {a}")
        if k < 2 or k > m:
            raise ValueError(f"成功线需 2-{m}，收到 {k}")

        return {"n": n, "a": a, "k": k, "m": m}

    # ── 格式化输出 ──

    def _fmt(self, result: dict, n: int, a: int, k: int, m: int) -> str:
        pools = result["pools"]
        successes = result["successes"]
        total = result["total_dice"]
        ones = result["ones"]
        maxes = result["maxes"]
        extra = result["extra_rounds"]

        lines = [f"骰池: {n}A{a}K{k}M{m}"]

        # 筛子数量不多时显示逐轮详情
        if total <= 30:
            for i, pool in enumerate(pools):
                label = "初掷" if i == 0 else f"加骰第{i}轮"
                parts = []
                for d in pool:
                    v, t, s = d["v"], d["t"], d["s"]
                    if t and s: parts.append(f"[{v}]")
                    elif t and not s: parts.append(f"{{{v}}}")
                    elif s and not t: parts.append(f"*{v}*")
                    else: parts.append(str(v))
                lines.append(f"  {label}: " + " ".join(parts))
        else:
            # 大骰池只显示摘要
            lines.append(f"  (共{total}骰，省略逐轮详情)")

        lines.append(f"成功: {successes}  |  出1: {ones}次  |  出{m}: {maxes}次  |  加骰: {extra}轮")
        if successes == 0:
            lines.append("💀 大失败！")
        return "\n".join(lines)

    # ── set 命令 ──

    def _handle_set(self, raw: str, defaults: dict) -> str:
        parts = raw.strip().split()
        if len(parts) < 2:
            return "用法: .ww set [k6] [a8] [m9] 或 .ww set clr"
        sub = " ".join(parts[1:]).strip()
        if sub == "clr":
            self._save_defaults({"a": DEFAULT_A, "k": DEFAULT_K, "m": DEFAULT_M})
            return f"已重置默认设定 → A={DEFAULT_A}, K={DEFAULT_K}, M={DEFAULT_M}"

        new_a, new_k, new_m = defaults["a"], defaults["k"], defaults["m"]
        for m_obj in re.finditer(r"([akm])(\d+)", sub):
            key, val = m_obj.group(1), int(m_obj.group(2))
            if key == "a": new_a = val
            elif key == "k": new_k = val
            elif key == "m": new_m = val
        if new_a > new_m:
            return f"错误: 加骰线 ({new_a}) 不能大于面数 ({new_m})"
        if new_k > new_m:
            return f"错误: 成功线 ({new_k}) 不能大于面数 ({new_m})"

        self._save_defaults({"a": new_a, "k": new_k, "m": new_m})
        return f"默认设定已更新 → A={new_a}, K={new_k}, M={new_m}"
