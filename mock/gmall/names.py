"""确定性随机中文姓名 / 昵称 / 邮箱池（复刻原 jar RandomName / RandomEmail 行为）。

全部函数从调用方传入的 random.Random 消费随机数，不使用全局随机，
保证种子 42 下逐次可复现。
"""

from __future__ import annotations

import random
import string

# 常见姓氏（复刻 RandomName.getChineseFamilyName 的常见百家姓缩池）
FAMILY_NAMES = [
    "王",
    "李",
    "张",
    "刘",
    "陈",
    "杨",
    "黄",
    "赵",
    "吴",
    "周",
    "徐",
    "孙",
    "马",
    "朱",
    "胡",
    "郭",
    "何",
    "林",
    "罗",
    "高",
    "郑",
    "梁",
    "谢",
    "宋",
    "唐",
    "许",
    "韩",
    "冯",
    "邓",
    "曹",
    "彭",
    "曾",
    "肖",
    "田",
    "董",
    "潘",
    "袁",
    "蒋",
    "蔡",
    "余",
]

MALE_GIVEN_NAMES = [
    "伟",
    "强",
    "军",
    "磊",
    "勇",
    "杰",
    "涛",
    "明",
    "超",
    "辉",
    "浩然",
    "子轩",
    "宇轩",
    "博文",
    "天佑",
    "志远",
    "建华",
    "国庆",
]

FEMALE_GIVEN_NAMES = [
    "芳",
    "娜",
    "敏",
    "静",
    "丽",
    "娟",
    "艳",
    "秀英",
    "雪",
    "慧",
    "雨桐",
    "欣怡",
    "梓涵",
    "梦琪",
    "语嫣",
    "思琪",
    "佳琪",
    "雅静",
]

NICKNAME_PREFIX = ["快乐", "小", "大", "爱", "超级", "幸福", "元气", "微笑"]
NICKNAME_SUFFIX = ["的猫", "星球", "小屋", "日记", "先森", "女士", "君", "酱", ""]


def _given_name(rng: random.Random, gender: str) -> str:
    pool = MALE_GIVEN_NAMES if gender == "M" else FEMALE_GIVEN_NAMES
    return rng.choice(pool)


def gen_name(rng: random.Random, gender: str) -> str:
    """生成中文姓名：姓氏 + 性别名。"""
    return rng.choice(FAMILY_NAMES) + _given_name(rng, gender)


def inside_last_name(rng: random.Random, gender: str) -> str:
    """生成不含姓氏的名（原 RandomName.insideLastName）。"""
    return _given_name(rng, gender)


def gen_nick_name(rng: random.Random, gender: str, last_name: str) -> str:
    """生成昵称：前缀 + 名字 + 后缀 组合（原 RandomName.getNickName 风格）。"""
    return rng.choice(NICKNAME_PREFIX) + last_name + rng.choice(NICKNAME_SUFFIX)


_EMAIL_DOMAINS = ["qq.com", "163.com", "126.com", "gmail.com", "outlook.com"]


def gen_email(rng: random.Random, min_len: int = 6, max_len: int = 12) -> str:
    """生成随机邮箱（原 RandomEmail.getEmail：字母开头 + 长度区间 + 常见域名）。"""
    length = rng.randint(min_len, max_len)
    local = rng.choice(string.ascii_lowercase) + "".join(
        rng.choice(string.ascii_lowercase + string.digits) for _ in range(length - 1)
    )
    return f"{local}@{rng.choice(_EMAIL_DOMAINS)}"


def gen_digits(rng: random.Random, n: int) -> str:
    """生成 n 位数字字符串（原 RandomNumString）。"""
    return "".join(rng.choice(string.digits) for _ in range(n))
