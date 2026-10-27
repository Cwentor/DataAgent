-- ClickHouse 本地编排的初始化脚本（docker/docker-compose.backends.yml 挂载到
-- /docker-entrypoint-initdb.d，官方 entrypoint 在 server 就绪后按文件名顺序执行）。
--
-- 为什么建库/建账号必须走 SQL 而不能写进 users.d XML：
-- 1. 建仓前 gmall 库不存在，XML 的 <grants> 指向不存在的库会让 server 启动失败；
-- 2. users.d 定义的用户落在 users_xml 存储，该存储对 GRANT 只读
--    （实锤：ACCESS_STORAGE_READONLY: Cannot update user in users_xml because
--    this storage is readonly），只有 SQL 建的用户落 local_directory 存储可授权。
-- profile（readonly=1 / 预算）仍由 .github/clickhouse/users.d/dataagent_ro.xml
-- 承载——readonly 会话内无法 SET，必须由服务端 profile 固化。
--
-- 账号口径：密码为本地/CI 演示值，与 mysql root/root 同级别，禁止复用到生产。
-- 生产按 spec §9 收窄为 gmall.* + system.* 并保持两账号分离。

CREATE DATABASE IF NOT EXISTS gmall;

CREATE USER IF NOT EXISTS dataagent_ro
    IDENTIFIED WITH sha256_password BY 'ch_ro_pwd'
    SETTINGS PROFILE 'ch_ro_profile';

-- harden() 校验要读 system.settings；catalog_loader 走 system.columns。
-- 本地取宽口径与 CI 一致（GRANT SELECT ON *.*），生产收窄。
GRANT SELECT ON *.* TO dataagent_ro;
