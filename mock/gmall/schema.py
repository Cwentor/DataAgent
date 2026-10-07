"""Gmall 测试数仓 DDL（DuckDB）。

表清单与字段对照 docs/plans/2026-10-06-gmall-mock-reverse-spec.md §3：
- 种子表 7 类（直接取自 upstream/gmall.sql 的教学库数据）；
- 生成表 11 张（用户/购物车/收藏/领券/订单/明细/支付/退款/退款支付/评论/状态日志）；
- 行为事实表 4 张（由埋点 JSON 日志解析入仓，非直接生成）；
- 元数据表 2 张（_field_metadata / _table_metadata）。
"""

from __future__ import annotations

DDL_STATEMENTS: list[str] = [
    # ---------------- 种子表 ----------------
    """
    CREATE TABLE base_province (
        province_id     INTEGER PRIMARY KEY,
        province_name   VARCHAR,
        region_id       INTEGER,
        area_code       VARCHAR,
        iso_code        VARCHAR,
        iso_3166_2      VARCHAR,
        create_time     TIMESTAMP,
        operate_time    TIMESTAMP
    )
    """,
    """
    CREATE TABLE base_region (
        region_id    INTEGER PRIMARY KEY,
        region_name  VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE base_category1 (
        category1_id INTEGER PRIMARY KEY,
        category1_name VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE base_category2 (
        category2_id INTEGER PRIMARY KEY,
        category2_name VARCHAR,
        category1_id INTEGER,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE base_category3 (
        category3_id INTEGER PRIMARY KEY,
        category3_name VARCHAR,
        category2_id INTEGER,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE base_trademark (
        tm_id        INTEGER PRIMARY KEY,
        tm_name      VARCHAR,
        logo_url     VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE spu_info (
        spu_id       INTEGER PRIMARY KEY,
        spu_name     VARCHAR,
        description  VARCHAR,
        category3_id INTEGER,
        tm_id        INTEGER,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE sku_info (
        sku_id       INTEGER PRIMARY KEY,
        spu_id       INTEGER,
        price        DECIMAL(10,2),
        sku_name     VARCHAR,
        sku_desc     VARCHAR,
        weight       DECIMAL(10,2),
        tm_id        INTEGER,
        category3_id INTEGER,
        sku_default_img VARCHAR,
        is_sale      INTEGER,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE coupon_info (
        coupon_id        INTEGER PRIMARY KEY,
        coupon_name      VARCHAR,
        coupon_type      VARCHAR,
        condition_amount DECIMAL(16,2),
        condition_num    BIGINT,
        activity_id      BIGINT,
        benefit_amount   DECIMAL(16,2),
        benefit_discount DECIMAL(10,2),
        create_time      TIMESTAMP,
        range_type       VARCHAR,
        limit_num        INTEGER,
        taken_count      INTEGER,
        start_time       TIMESTAMP,
        end_time         TIMESTAMP,
        operate_time     TIMESTAMP,
        expire_time      TIMESTAMP,
        range_desc       VARCHAR
    )
    """,
    """
    CREATE TABLE coupon_range (
        id           INTEGER PRIMARY KEY,
        coupon_id    INTEGER,
        range_type   VARCHAR,
        range_id     BIGINT,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE activity_info (
        activity_id   INTEGER PRIMARY KEY,
        activity_name VARCHAR,
        activity_type VARCHAR,
        activity_desc VARCHAR,
        start_time    TIMESTAMP,
        end_time      TIMESTAMP,
        create_time   TIMESTAMP,
        operate_time  TIMESTAMP
    )
    """,
    """
    CREATE TABLE activity_rule (
        rule_id          INTEGER PRIMARY KEY,
        activity_id      INTEGER,
        activity_type    VARCHAR,
        condition_amount DECIMAL(16,2),
        condition_num    BIGINT,
        benefit_amount   DECIMAL(16,2),
        benefit_discount DECIMAL(10,2),
        benefit_level    BIGINT,
        create_time      TIMESTAMP,
        operate_time     TIMESTAMP
    )
    """,
    """
    CREATE TABLE activity_sku (
        id           INTEGER PRIMARY KEY,
        activity_id  BIGINT,
        sku_id       BIGINT,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    # ---------------- 生成表 ----------------
    """
    CREATE TABLE user_info (
        user_id      BIGINT PRIMARY KEY,
        login_name   VARCHAR,
        nick_name    VARCHAR,
        name         VARCHAR,
        phone_num    VARCHAR,
        email        VARCHAR,
        user_level   VARCHAR,
        birthday     DATE,
        gender       VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE cart_info (
        cart_id      BIGINT PRIMARY KEY,
        user_id      BIGINT,
        sku_id       BIGINT,
        cart_price   DECIMAL(10,2),
        sku_num      BIGINT,
        sku_name     VARCHAR,
        is_ordered   INTEGER,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP,
        order_time   TIMESTAMP
    )
    """,
    """
    CREATE TABLE favor_info (
        favor_id     BIGINT PRIMARY KEY,
        user_id      BIGINT,
        sku_id       BIGINT,
        spu_id       BIGINT,
        is_cancel    VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE coupon_use (
        coupon_use_id BIGINT PRIMARY KEY,
        coupon_id     BIGINT,
        user_id       BIGINT,
        order_id      BIGINT,
        coupon_status VARCHAR,
        get_time      TIMESTAMP,
        using_time    TIMESTAMP,
        used_time     TIMESTAMP,
        expire_time   TIMESTAMP,
        create_time   TIMESTAMP,
        operate_time  TIMESTAMP
    )
    """,
    """
    CREATE TABLE order_info (
        order_id               BIGINT PRIMARY KEY,
        out_trade_no           VARCHAR,
        user_id                BIGINT,
        province_id            INTEGER,
        consignee              VARCHAR,
        consignee_tel          VARCHAR,
        trade_body             VARCHAR,
        original_total_amount  DECIMAL(16,2),
        coupon_reduce_amount   DECIMAL(16,2),
        activity_reduce_amount DECIMAL(16,2),
        total_amount           DECIMAL(16,2),
        feight_fee             DECIMAL(10,2),
        payment_way            VARCHAR,
        order_status           VARCHAR,
        create_time            TIMESTAMP,
        operate_time           TIMESTAMP,
        expire_time            TIMESTAMP,
        refundable_time        TIMESTAMP
    )
    """,
    """
    CREATE TABLE order_detail (
        order_detail_id       BIGINT PRIMARY KEY,
        order_id              BIGINT,
        order_status          VARCHAR,
        user_id               BIGINT,
        province_id           INTEGER,
        sku_id                BIGINT,
        sku_name              VARCHAR,
        order_price           DECIMAL(10,2),
        sku_num               BIGINT,
        split_total_amount    DECIMAL(16,2),
        split_coupon_amount   DECIMAL(16,2),
        split_activity_amount DECIMAL(16,2),
        order_time            TIMESTAMP,
        tm_id                 INTEGER,
        tm_name               VARCHAR,
        category3_id          INTEGER,
        category3_name        VARCHAR,
        category2_id          INTEGER,
        category2_name        VARCHAR,
        category1_id          INTEGER,
        category1_name        VARCHAR
    )
    """,
    """
    CREATE TABLE payment_info (
        payment_id       BIGINT PRIMARY KEY,
        out_trade_no     VARCHAR,
        order_id         BIGINT,
        user_id          BIGINT,
        total_amount     DECIMAL(16,2),
        subject          VARCHAR,
        payment_type     VARCHAR,
        payment_status   VARCHAR,
        create_time      TIMESTAMP,
        callback_time    TIMESTAMP,
        callback_content VARCHAR,
        operate_time     TIMESTAMP
    )
    """,
    """
    CREATE TABLE order_refund_info (
        refund_id          BIGINT PRIMARY KEY,
        user_id            BIGINT,
        order_id           BIGINT,
        sku_id             BIGINT,
        refund_type        VARCHAR,
        refund_num         BIGINT,
        refund_amount      DECIMAL(16,2),
        refund_reason_type VARCHAR,
        refund_reason_txt  VARCHAR,
        refund_status      VARCHAR,
        create_time        TIMESTAMP,
        operate_time       TIMESTAMP
    )
    """,
    """
    CREATE TABLE refund_payment (
        refund_payment_id BIGINT PRIMARY KEY,
        order_id          BIGINT,
        sku_id            BIGINT,
        payment_type      VARCHAR,
        trade_no          VARCHAR,
        total_amount      DECIMAL(16,2),
        subject           VARCHAR,
        refund_status     VARCHAR,
        create_time       TIMESTAMP,
        callback_time     TIMESTAMP,
        callback_content  VARCHAR,
        operate_time      TIMESTAMP
    )
    """,
    """
    CREATE TABLE comment_info (
        comment_id   BIGINT PRIMARY KEY,
        user_id      BIGINT,
        sku_id       BIGINT,
        spu_id       BIGINT,
        order_id     BIGINT,
        appraise     VARCHAR,
        nick_name    VARCHAR,
        head_img     VARCHAR,
        comment_txt  VARCHAR,
        create_time  TIMESTAMP,
        operate_time TIMESTAMP
    )
    """,
    """
    CREATE TABLE order_status_log (
        status_log_id BIGINT PRIMARY KEY,
        order_id      BIGINT,
        order_status  VARCHAR,
        create_time   TIMESTAMP,
        operate_time  TIMESTAMP
    )
    """,
    # ---------------- 行为事实表（埋点日志解析产物） ----------------
    """
    CREATE TABLE fact_start (
        sid             VARCHAR,
        mid             VARCHAR,
        uid             VARCHAR,
        province_id     INTEGER,
        channel         VARCHAR,
        os              VARCHAR,
        entry           VARCHAR,
        open_ad_ms      INTEGER,
        open_ad_skip_ms INTEGER,
        loading_time_ms INTEGER,
        ts              TIMESTAMP
    )
    """,
    """
    CREATE TABLE fact_page_view (
        sid          VARCHAR,
        mid          VARCHAR,
        uid          VARCHAR,
        province_id  INTEGER,
        page_id      VARCHAR,
        last_page_id VARCHAR,
        during_time  INTEGER,
        item         VARCHAR,
        item_type    VARCHAR,
        from_pos_id  INTEGER,
        from_pos_seq INTEGER,
        err_code     INTEGER,
        ts           TIMESTAMP
    )
    """,
    """
    CREATE TABLE fact_action (
        sid       VARCHAR,
        action_id VARCHAR,
        item      VARCHAR,
        item_type VARCHAR,
        page_id   VARCHAR,
        uid       VARCHAR,
        ts        TIMESTAMP
    )
    """,
    """
    CREATE TABLE fact_display (
        sid       VARCHAR,
        item      VARCHAR,
        item_type VARCHAR,
        pos_id    INTEGER,
        pos_seq   INTEGER,
        page_id   VARCHAR,
        uid       VARCHAR,
        ts        TIMESTAMP
    )
    """,
]


def create_tables(conn) -> None:
    """在给定连接上按序执行全部 DDL（假定空库）。"""
    for ddl in DDL_STATEMENTS:
        conn.execute(ddl)
