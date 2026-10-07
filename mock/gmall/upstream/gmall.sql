/*
 Navicat Premium Data Transfer

 Source Server         : hadoop102
 Source Server Type    : MySQL
 Source Server Version : 80031 (8.0.31)
 Source Host           : hadoop102:3306
 Source Schema         : gmall

 Target Server Type    : MySQL
 Target Server Version : 80031 (8.0.31)
 File Encoding         : 65001

 Date: 17/05/2023 14:27:23
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for activity_info
-- ----------------------------
DROP TABLE IF EXISTS `activity_info`;
CREATE TABLE `activity_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '活动id',
  `activity_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '活动名称',
  `activity_type` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '活动类型（1：满减，2：折扣）',
  `activity_desc` varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '活动描述',
  `start_time` datetime NULL DEFAULT NULL COMMENT '开始时间',
  `end_time` datetime NULL DEFAULT NULL COMMENT '结束时间',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '活动表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of activity_info
-- ----------------------------
INSERT INTO `activity_info` VALUES (1, '小米手机专场', '3101', '小米手机满减2', '2022-01-13 01:01:54', '2023-06-19 00:00:00', '2022-05-27 00:00:00', NULL);
INSERT INTO `activity_info` VALUES (2, 'CAREMiLLE口红满两个8折', '3102', 'CAREMiLLE口红满两个8折', '2022-01-13 01:01:54', '2023-06-19 00:00:00', '2022-05-27 00:00:00', NULL);
INSERT INTO `activity_info` VALUES (3, '联想活动专场满减', '3101', '联想活动专场满减', '2022-01-13 01:01:54', '2023-06-19 00:00:00', '2022-05-27 00:00:00', NULL);
INSERT INTO `activity_info` VALUES (4, 'TCL全场9折', '3103', 'TCL全场9折', '2022-01-13 01:01:54', '2023-06-19 00:00:00', '2022-05-27 00:00:00', NULL);

-- ----------------------------
-- Table structure for activity_rule
-- ----------------------------
DROP TABLE IF EXISTS `activity_rule`;
CREATE TABLE `activity_rule`  (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '编号',
  `activity_id` int NULL DEFAULT NULL COMMENT '活动id',
  `activity_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '活动类型',
  `condition_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '满减金额',
  `condition_num` bigint NULL DEFAULT NULL COMMENT '满减件数',
  `benefit_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '优惠金额',
  `benefit_discount` decimal(10, 2) NULL DEFAULT NULL COMMENT '优惠折扣',
  `benefit_level` bigint NULL DEFAULT NULL COMMENT '优惠级别',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '活动规则表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of activity_rule
-- ----------------------------
INSERT INTO `activity_rule` VALUES (1, 1, '3101', 5000.00, NULL, 500.00, NULL, 1, '2022-02-13 01:08:32', NULL);
INSERT INTO `activity_rule` VALUES (2, 1, '3101', 8000.00, NULL, 900.00, NULL, 2, '2022-02-13 01:08:32', NULL);
INSERT INTO `activity_rule` VALUES (3, 2, '3102', NULL, 2, NULL, 8.50, 1, '2022-02-13 01:08:32', NULL);
INSERT INTO `activity_rule` VALUES (4, 3, '3101', 5000.00, NULL, 250.00, NULL, 1, '2022-02-13 01:08:32', NULL);
INSERT INTO `activity_rule` VALUES (5, 4, '3103', NULL, NULL, NULL, 9.00, 1, '2022-02-13 01:08:32', NULL);

-- ----------------------------
-- Table structure for activity_sku
-- ----------------------------
DROP TABLE IF EXISTS `activity_sku`;
CREATE TABLE `activity_sku`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `activity_id` bigint NULL DEFAULT NULL COMMENT '活动id ',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '活动商品关联表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of activity_sku
-- ----------------------------
INSERT INTO `activity_sku` VALUES (1, 1, 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (2, 1, 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (3, 1, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (4, 2, 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (5, 2, 27, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (6, 2, 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (7, 3, 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (8, 3, 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (9, 3, 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (10, 3, 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (11, 4, 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (12, 4, 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `activity_sku` VALUES (13, 4, 19, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_attr_info
-- ----------------------------
DROP TABLE IF EXISTS `base_attr_info`;
CREATE TABLE `base_attr_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `attr_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '属性名称',
  `category_id` bigint NULL DEFAULT NULL COMMENT '分类id',
  `category_level` int NULL DEFAULT NULL COMMENT '分类层级',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '属性表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_attr_info
-- ----------------------------
INSERT INTO `base_attr_info` VALUES (23, '运行内存33', 61, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (24, '机身内存', 61, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (25, '处理器', 287, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (26, '显卡', 287, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (37, '分类', 1, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (45, '产地', 371, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (46, '运行内存', 63, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (65, '品牌', 65, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (66, '硬盘', 287, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (67, '内存', 287, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (73, '像素', 178, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (74, '传感器尺寸', 178, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (76, '镜头', 178, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (81, '机身内存', 63, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (83, '价格', 100, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (84, '价格', 100, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (85, '价格3', 100, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (86, '价格', 102, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (87, '价格', 841, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (89, '电视类型', 86, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (90, '价格', 930, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (91, '价格', 598, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (92, '价格', 598, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (93, '价格', 598, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (94, '价格', 598, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (95, '价格', 997, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (96, '屏幕尺寸', 86, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (97, '分辨率', 86, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (98, '观看距离', 86, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (99, '画幅', 180, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (103, '面料', 233, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (105, '品牌', 62, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (106, '手机一级', 2, 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (107, '二级手机', 13, 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (108, '粮食调味', 803, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (109, '是否有机', 803, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (110, '分类', 803, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (111, '功效', 477, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (112, '香水彩妆', 477, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (113, '面部护肤', 477, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_info` VALUES (114, '香调', 473, 3, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_attr_value
-- ----------------------------
DROP TABLE IF EXISTS `base_attr_value`;
CREATE TABLE `base_attr_value`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `value_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '属性值名称',
  `attr_id` bigint NULL DEFAULT NULL COMMENT '属性id',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '属性值表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_attr_value
-- ----------------------------
INSERT INTO `base_attr_value` VALUES (1, '红色', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (2, '白色', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (3, '红色', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (4, '白色', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (5, '华为', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (6, '联想', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (7, 'X5', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (8, 'X6', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (9, 'X7', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (10, '3.0', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (11, '4.0', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (12, '5.0', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (13, '3G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (14, '4G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (19, 'i7', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (20, 'i9', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (21, 'RTX3070Ti', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (22, 'RTX30760', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (35, '123123', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (36, '222222', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (37, '红色', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (38, '白色', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (39, '红色', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (40, '白色', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (41, '南非', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (64, '16G', 46, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (69, '10', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (71, '10', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (72, '10', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (73, '10', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (78, '国内', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (79, '南非', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (83, '8G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (84, '512GB', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (85, '512GB+2T', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (86, '16G', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (87, '32G', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (89, '3434', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (90, '5.0', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (91, '5.6', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (93, '国内', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (94, '国外', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (95, '国内', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (96, '国外', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (97, '100W', 72, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (99, '2000万', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (100, '3000万', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (101, '1英寸', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (102, '2.3英寸', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (115, '1000G', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (120, '12G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (121, '5.5', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (122, '6.5', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (123, '123', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (124, '123', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (125, '123', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (126, '234', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (128, '234', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (129, '2000', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (130, '4000', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (132, '2000', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (133, '88888', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (135, '200', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (136, '4K超清', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (139, '2000', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (140, '2000', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (141, '3000', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (143, '400', 95, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (144, '70-75', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (145, '75以上', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (146, '8K超高清', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (147, '4K超高清', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (148, '12K超高清', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (149, '3.5m', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (150, '3m', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (151, 'APS-C画幅全画', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (152, '全画幅', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (162, '棉', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (163, '麻', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (164, '200W', 72, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (165, '2000', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (169, '6G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (170, '128G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (171, '256G', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (172, '国内', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (174, '国外', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (175, '苹果手机', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (176, '安卓手机', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (177, '小米', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (178, '华为', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (179, '苹果', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (180, '32G', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (181, '64G', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (182, '128G', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (183, '256G', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (184, '512G', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (185, '1T', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (187, 'Java', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (188, 'C#', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (189, 'JavaScript', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (190, 'rtrt', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (191, '曲面', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (192, '超薄', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (193, '人工智能', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (194, '60以下', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (195, '60-70', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (196, '杂粮', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (197, '大米', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (198, '面食', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (199, '调味品', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (200, '有机食品', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (201, '非有机食品', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (202, '杂粮组合', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (203, '米', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (204, '面', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (205, '黑米', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (206, '滋润', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (207, '保湿', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (208, '防水', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (209, '防晒', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (210, '口红', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (211, '香水', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (212, '润唇膏', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (213, '乳液/面霜', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (214, '面部精华', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (215, '花果香调', 114, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (216, '海洋香调', 114, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (217, '古龙香调', 114, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_attr_value` VALUES (218, '花香调', 114, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_category1
-- ----------------------------
DROP TABLE IF EXISTS `base_category1`;
CREATE TABLE `base_category1`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '一级品类id',
  `name` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '一级品类名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '一级品类表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_category1
-- ----------------------------
INSERT INTO `base_category1` VALUES (1, '图书、音像、电子书刊', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (2, '手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (3, '家用电器', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (4, '数码', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (5, '家居家装', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (6, '电脑办公', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (7, '厨具', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (8, '个护化妆', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (9, '服饰内衣', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (10, '钟表', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (11, '鞋靴', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (12, '母婴', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (13, '礼品箱包', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (14, '食品饮料、保健食品', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (15, '珠宝', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (16, '汽车用品', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category1` VALUES (17, '运动健康', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_category2
-- ----------------------------
DROP TABLE IF EXISTS `base_category2`;
CREATE TABLE `base_category2`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '二级品类id',
  `name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '二级品类名称',
  `category1_id` bigint NULL DEFAULT NULL COMMENT '一级品类编号',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '二级品类表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_category2
-- ----------------------------
INSERT INTO `base_category2` VALUES (1, '电子书刊', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (2, '音像', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (3, '英文原版', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (4, '文艺', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (5, '少儿', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (6, '人文社科', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (7, '经管励志', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (8, '生活', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (9, '科技', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (10, '教育', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (11, '港台图书', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (12, '其他', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (13, '手机通讯', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (14, '运营商', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (15, '手机配件', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (16, '大 家 电', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (17, '厨卫大电', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (18, '厨房小电', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (19, '生活电器', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (20, '个护健康', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (21, '五金家装', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (22, '摄影摄像', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (23, '数码配件', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (24, '智能设备', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (25, '影音娱乐', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (26, '电子教育', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (27, '虚拟商品', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (28, '家纺', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (29, '灯具', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (30, '生活日用', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (31, '家装软饰', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (32, '宠物生活', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (33, '电脑整机', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (34, '电脑配件', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (35, '外设产品', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (36, '游戏设备', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (37, '网络产品', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (38, '办公设备', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (39, '文具/耗材', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (40, '服务产品', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (41, '烹饪锅具', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (42, '刀剪菜板', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (43, '厨房配件', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (44, '水具酒具', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (45, '餐具', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (46, '酒店用品', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (47, '茶具/咖啡具', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (48, '清洁用品', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (49, '面部护肤', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (50, '身体护理', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (51, '口腔护理', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (52, '女性护理', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (53, '洗发护发', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (54, '香水彩妆', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (55, '女装', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (56, '男装', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (57, '内衣', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (58, '洗衣服务', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (59, '服饰配件', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (60, '钟表', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (61, '流行男鞋', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (62, '时尚女鞋', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (63, '奶粉', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (64, '营养辅食', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (65, '尿裤湿巾', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (66, '喂养用品', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (67, '洗护用品', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (68, '童车童床', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (69, '寝居服饰', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (70, '妈妈专区', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (71, '童装童鞋', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (72, '安全座椅', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (73, '潮流女包', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (74, '精品男包', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (75, '功能箱包', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (76, '礼品', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (77, '奢侈品', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (78, '婚庆', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (79, '进口食品', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (80, '地方特产', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (81, '休闲食品', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (82, '粮油调味', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (83, '饮料冲调', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (84, '食品礼券', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (85, '茗茶', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (86, '时尚饰品', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (87, '黄金', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (88, 'K金饰品', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (89, '金银投资', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (90, '银饰', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (91, '钻石', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (92, '翡翠玉石', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (93, '水晶玛瑙', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (94, '彩宝', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (95, '铂金', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (96, '木手串/把件', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (97, '珍珠', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (98, '维修保养', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (99, '车载电器', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (100, '美容清洗', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (101, '汽车装饰', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (102, '安全自驾', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (103, '汽车服务', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (104, '赛事改装', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (105, '运动鞋包', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (106, '运动服饰', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (107, '骑行运动', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (108, '垂钓用品', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (109, '游泳用品', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (110, '户外鞋服', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (111, '户外装备', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (112, '健身训练', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category2` VALUES (113, '体育用品', 17, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_category3
-- ----------------------------
DROP TABLE IF EXISTS `base_category3`;
CREATE TABLE `base_category3`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '三级品类id',
  `name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '三级品类名称',
  `category2_id` bigint NULL DEFAULT NULL COMMENT '二级品类编号',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '三级品类表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_category3
-- ----------------------------
INSERT INTO `base_category3` VALUES (1, '电子书', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (2, '网络原创', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (3, '数字杂志', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (4, '多媒体图书', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (5, '音乐', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (6, '影视', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (7, '教育音像', 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (8, '少儿', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (9, '商务投资', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (10, '英语学习与考试', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (11, '文学', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (12, '传记', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (13, '励志', 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (14, '小说', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (15, '文学', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (16, '青春文学', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (17, '传记', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (18, '艺术', 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (19, '少儿', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (20, '0-2岁', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (21, '3-6岁', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (22, '7-10岁', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (23, '11-14岁', 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (24, '历史', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (25, '哲学', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (26, '国学', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (27, '政治/军事', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (28, '法律', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (29, '人文社科', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (30, '心理学', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (31, '文化', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (32, '社会科学', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (33, '经济', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (34, '金融与投资', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (35, '管理', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (36, '励志与成功', 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (37, '生活', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (38, '健身与保健', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (39, '家庭与育儿', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (40, '旅游', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (41, '烹饪美食', 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (42, '工业技术', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (43, '科普读物', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (44, '建筑', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (45, '医学', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (46, '科学与自然', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (47, '计算机与互联网', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (48, '电子通信', 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (49, '中小学教辅', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (50, '教育与考试', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (51, '外语学习', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (52, '大中专教材', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (53, '字典词典', 10, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (54, '艺术/设计/收藏', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (55, '经济管理', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (56, '文化/学术', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (57, '少儿', 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (58, '工具书', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (59, '杂志/期刊', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (60, '套装书', 12, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (61, '手机', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (62, '对讲机', 13, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (63, '合约机', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (64, '选号中心', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (65, '装宽带', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (66, '办套餐', 14, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (67, '移动电源', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (68, '电池/移动电源', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (69, '蓝牙耳机', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (70, '充电器/数据线', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (71, '苹果周边', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (72, '手机耳机', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (73, '手机贴膜', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (74, '手机存储卡', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (75, '充电器', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (76, '数据线', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (77, '手机保护套', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (78, '车载配件', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (79, 'iPhone 配件', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (80, '手机电池', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (81, '创意配件', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (82, '便携/无线音响', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (83, '手机饰品', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (84, '拍照配件', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (85, '手机支架', 15, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (86, '平板电视', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (87, '空调', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (88, '冰箱', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (89, '洗衣机', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (90, '家庭影院', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (91, 'DVD/电视盒子', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (92, '迷你音响', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (93, '冷柜/冰吧', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (94, '家电配件', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (95, '功放', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (96, '回音壁/Soundbar', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (97, 'Hi-Fi专区', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (98, '电视盒子', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (99, '酒柜', 16, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (100, '燃气灶', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (101, '油烟机', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (102, '热水器', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (103, '消毒柜', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (104, '洗碗机', 17, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (105, '料理机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (106, '榨汁机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (107, '电饭煲', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (108, '电压力锅', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (109, '豆浆机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (110, '咖啡机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (111, '微波炉', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (112, '电烤箱', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (113, '电磁炉', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (114, '面包机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (115, '煮蛋器', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (116, '酸奶机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (117, '电炖锅', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (118, '电水壶/热水瓶', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (119, '电饼铛', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (120, '多用途锅', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (121, '电烧烤炉', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (122, '果蔬解毒机', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (123, '其它厨房电器', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (124, '养生壶/煎药壶', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (125, '电热饭盒', 18, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (126, '取暖电器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (127, '净化器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (128, '加湿器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (129, '扫地机器人', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (130, '吸尘器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (131, '挂烫机/熨斗', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (132, '插座', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (133, '电话机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (134, '清洁机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (135, '除湿机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (136, '干衣机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (137, '收录/音机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (138, '电风扇', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (139, '冷风扇', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (140, '其它生活电器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (141, '生活电器配件', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (142, '净水器', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (143, '饮水机', 19, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (144, '剃须刀', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (145, '剃/脱毛器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (146, '口腔护理', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (147, '电吹风', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (148, '美容器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (149, '理发器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (150, '卷/直发器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (151, '按摩椅', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (152, '按摩器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (153, '足浴盆', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (154, '血压计', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (155, '电子秤/厨房秤', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (156, '血糖仪', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (157, '体温计', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (158, '其它健康电器', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (159, '计步器/脂肪检测仪', 20, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (160, '电动工具', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (161, '手动工具', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (162, '仪器仪表', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (163, '浴霸/排气扇', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (164, '灯具', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (165, 'LED灯', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (166, '洁身器', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (167, '水槽', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (168, '龙头', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (169, '淋浴花洒', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (170, '厨卫五金', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (171, '家具五金', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (172, '门铃', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (173, '电气开关', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (174, '插座', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (175, '电工电料', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (176, '监控安防', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (177, '电线/线缆', 21, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (178, '数码相机', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (179, '单电/微单相机', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (180, '单反相机', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (181, '摄像机', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (182, '拍立得', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (183, '运动相机', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (184, '镜头', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (185, '户外器材', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (186, '影棚器材', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (187, '冲印服务', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (188, '数码相框', 22, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (189, '存储卡', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (190, '读卡器', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (191, '滤镜', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (192, '闪光灯/手柄', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (193, '相机包', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (194, '三脚架/云台', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (195, '相机清洁/贴膜', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (196, '机身附件', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (197, '镜头附件', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (198, '电池/充电器', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (199, '移动电源', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (200, '数码支架', 23, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (201, '智能手环', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (202, '智能手表', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (203, '智能眼镜', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (204, '运动跟踪器', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (205, '健康监测', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (206, '智能配饰', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (207, '智能家居', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (208, '体感车', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (209, '其他配件', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (210, '智能机器人', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (211, '无人机', 24, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (212, 'MP3/MP4', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (213, '智能设备', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (214, '耳机/耳麦', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (215, '便携/无线音箱', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (216, '音箱/音响', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (217, '高清播放器', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (218, '收音机', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (219, 'MP3/MP4配件', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (220, '麦克风', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (221, '专业音频', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (222, '苹果配件', 25, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (223, '学生平板', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (224, '点读机/笔', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (225, '早教益智', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (226, '录音笔', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (227, '电纸书', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (228, '电子词典', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (229, '复读机', 26, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (230, '延保服务', 27, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (231, '杀毒软件', 27, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (232, '积分商品', 27, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (233, '桌布/罩件', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (234, '地毯地垫', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (235, '沙发垫套/椅垫', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (236, '床品套件', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (237, '被子', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (238, '枕芯', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (239, '床单被罩', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (240, '毯子', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (241, '床垫/床褥', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (242, '蚊帐', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (243, '抱枕靠垫', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (244, '毛巾浴巾', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (245, '电热毯', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (246, '窗帘/窗纱', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (247, '布艺软饰', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (248, '凉席', 28, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (249, '台灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (250, '节能灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (251, '装饰灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (252, '落地灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (253, '应急灯/手电', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (254, 'LED灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (255, '吸顶灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (256, '五金电器', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (257, '筒灯射灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (258, '吊灯', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (259, '氛围照明', 29, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (260, '保暖防护', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (261, '收纳用品', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (262, '雨伞雨具', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (263, '浴室用品', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (264, '缝纫/针织用品', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (265, '洗晒/熨烫', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (266, '净化除味', 30, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (267, '相框/照片墙', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (268, '装饰字画', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (269, '节庆饰品', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (270, '手工/十字绣', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (271, '装饰摆件', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (272, '帘艺隔断', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (273, '墙贴/装饰贴', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (274, '钟饰', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (275, '花瓶花艺', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (276, '香薰蜡烛', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (277, '创意家居', 31, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (278, '宠物主粮', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (279, '宠物零食', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (280, '医疗保健', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (281, '家居日用', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (282, '宠物玩具', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (283, '出行装备', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (284, '洗护美容', 32, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (285, '笔记本', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (286, '超极本', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (287, '游戏本', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (288, '平板电脑', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (289, '平板电脑配件', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (290, '台式机', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (291, '服务器/工作站', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (292, '笔记本配件', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (293, '一体机', 33, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (294, 'CPU', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (295, '主板', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (296, '显卡', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (297, '硬盘', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (298, 'SSD固态硬盘', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (299, '内存', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (300, '机箱', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (301, '电源', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (302, '显示器', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (303, '刻录机/光驱', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (304, '散热器', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (305, '声卡/扩展卡', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (306, '装机配件', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (307, '组装电脑', 34, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (308, '移动硬盘', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (309, 'U盘', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (310, '鼠标', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (311, '键盘', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (312, '鼠标垫', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (313, '摄像头', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (314, '手写板', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (315, '硬盘盒', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (316, '插座', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (317, '线缆', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (318, 'UPS电源', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (319, '电脑工具', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (320, '游戏设备', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (321, '电玩', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (322, '电脑清洁', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (323, '网络仪表仪器', 35, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (324, '游戏机', 36, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (325, '游戏耳机', 36, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (326, '手柄/方向盘', 36, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (327, '游戏软件', 36, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (328, '游戏周边', 36, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (329, '路由器', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (330, '网卡', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (331, '交换机', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (332, '网络存储', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (333, '4G/3G上网', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (334, '网络盒子', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (335, '网络配件', 37, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (336, '投影机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (337, '投影配件', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (338, '多功能一体机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (339, '打印机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (340, '传真设备', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (341, '验钞/点钞机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (342, '扫描设备', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (343, '复合机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (344, '碎纸机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (345, '考勤机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (346, '收款/POS机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (347, '会议音频视频', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (348, '保险柜', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (349, '装订/封装机', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (350, '安防监控', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (351, '办公家具', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (352, '白板', 38, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (353, '硒鼓/墨粉', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (354, '墨盒', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (355, '色带', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (356, '纸类', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (357, '办公文具', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (358, '学生文具', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (359, '财会用品', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (360, '文件管理', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (361, '本册/便签', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (362, '计算器', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (363, '笔类', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (364, '画具画材', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (365, '刻录碟片/附件', 39, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (366, '上门安装', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (367, '延保服务', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (368, '维修保养', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (369, '电脑软件', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (370, '京东服务', 40, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (371, '炒锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (372, '煎锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (373, '压力锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (374, '蒸锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (375, '汤锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (376, '奶锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (377, '锅具套装', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (378, '煲类', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (379, '水壶', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (380, '火锅', 41, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (381, '菜刀', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (382, '剪刀', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (383, '刀具套装', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (384, '砧板', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (385, '瓜果刀/刨', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (386, '多功能刀', 42, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (387, '保鲜盒', 43, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (388, '烘焙/烧烤', 43, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (389, '饭盒/提锅', 43, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (390, '储物/置物架', 43, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (391, '厨房DIY/小工具', 43, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (392, '塑料杯', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (393, '运动水壶', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (394, '玻璃杯', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (395, '陶瓷/马克杯', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (396, '保温杯', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (397, '保温壶', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (398, '酒杯/酒具', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (399, '杯具套装', 44, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (400, '餐具套装', 45, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (401, '碗/碟/盘', 45, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (402, '筷勺/刀叉', 45, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (403, '一次性用品', 45, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (404, '果盘/果篮', 45, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (405, '自助餐炉', 46, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (406, '酒店餐具', 46, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (407, '酒店水具', 46, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (408, '整套茶具', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (409, '茶杯', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (410, '茶壶', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (411, '茶盘茶托', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (412, '茶叶罐', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (413, '茶具配件', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (414, '茶宠摆件', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (415, '咖啡具', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (416, '其他', 47, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (417, '纸品湿巾', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (418, '衣物清洁', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (419, '清洁工具', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (420, '驱虫用品', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (421, '家庭清洁', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (422, '皮具护理', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (423, '一次性用品', 48, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (424, '洁面', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (425, '乳液面霜', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (426, '面膜', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (427, '剃须', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (428, '套装', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (429, '精华', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (430, '眼霜', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (431, '卸妆', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (432, '防晒', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (433, '防晒隔离', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (434, 'T区护理', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (435, '眼部护理', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (436, '精华露', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (437, '爽肤水', 49, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (438, '沐浴', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (439, '润肤', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (440, '颈部', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (441, '手足', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (442, '纤体塑形', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (443, '美胸', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (444, '套装', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (445, '精油', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (446, '洗发护发', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (447, '染发/造型', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (448, '香薰精油', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (449, '磨砂/浴盐', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (450, '手工/香皂', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (451, '洗发', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (452, '护发', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (453, '染发', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (454, '磨砂膏', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (455, '香皂', 50, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (456, '牙膏/牙粉', 51, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (457, '牙刷/牙线', 51, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (458, '漱口水', 51, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (459, '套装', 51, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (460, '卫生巾', 52, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (461, '卫生护垫', 52, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (462, '私密护理', 52, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (463, '脱毛膏', 52, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (464, '其他', 52, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (465, '洗发', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (466, '护发', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (467, '染发', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (468, '造型', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (469, '假发', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (470, '套装', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (471, '美发工具', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (472, '脸部护理', 53, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (473, '香水', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (474, '底妆', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (475, '腮红', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (476, '眼影', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (477, '唇部', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (478, '美甲', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (479, '眼线', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (480, '美妆工具', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (481, '套装', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (482, '防晒隔离', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (483, '卸妆', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (484, '眉笔', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (485, '睫毛膏', 54, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (486, 'T恤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (487, '衬衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (488, '针织衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (489, '雪纺衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (490, '卫衣', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (491, '马甲', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (492, '连衣裙', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (493, '半身裙', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (494, '牛仔裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (495, '休闲裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (496, '打底裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (497, '正装裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (498, '小西装', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (499, '短外套', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (500, '风衣', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (501, '毛呢大衣', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (502, '真皮皮衣', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (503, '棉服', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (504, '羽绒服', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (505, '大码女装', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (506, '中老年女装', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (507, '婚纱', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (508, '打底衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (509, '旗袍/唐装', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (510, '加绒裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (511, '吊带/背心', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (512, '羊绒衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (513, '短裤', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (514, '皮草', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (515, '礼服', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (516, '仿皮皮衣', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (517, '羊毛衫', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (518, '设计师/潮牌', 55, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (519, '衬衫', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (520, 'T恤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (521, 'POLO衫', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (522, '针织衫', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (523, '羊绒衫', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (524, '卫衣', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (525, '马甲/背心', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (526, '夹克', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (527, '风衣', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (528, '毛呢大衣', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (529, '仿皮皮衣', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (530, '西服', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (531, '棉服', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (532, '羽绒服', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (533, '牛仔裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (534, '休闲裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (535, '西裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (536, '西服套装', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (537, '大码男装', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (538, '中老年男装', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (539, '唐装/中山装', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (540, '工装', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (541, '真皮皮衣', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (542, '加绒裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (543, '卫裤/运动裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (544, '短裤', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (545, '设计师/潮牌', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (546, '羊毛衫', 56, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (547, '文胸', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (548, '女式内裤', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (549, '男式内裤', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (550, '睡衣/家居服', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (551, '塑身美体', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (552, '泳衣', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (553, '吊带/背心', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (554, '抹胸', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (555, '连裤袜/丝袜', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (556, '美腿袜', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (557, '商务男袜', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (558, '保暖内衣', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (559, '情侣睡衣', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (560, '文胸套装', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (561, '少女文胸', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (562, '休闲棉袜', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (563, '大码内衣', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (564, '内衣配件', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (565, '打底裤袜', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (566, '打底衫', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (567, '秋衣秋裤', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (568, '情趣内衣', 57, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (569, '服装洗护', 58, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (570, '太阳镜', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (571, '光学镜架/镜片', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (572, '围巾/手套/帽子套装', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (573, '袖扣', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (574, '棒球帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (575, '毛线帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (576, '遮阳帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (577, '老花镜', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (578, '装饰眼镜', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (579, '防辐射眼镜', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (580, '游泳镜', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (581, '女士丝巾/围巾/披肩', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (582, '男士丝巾/围巾', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (583, '鸭舌帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (584, '贝雷帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (585, '礼帽', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (586, '真皮手套', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (587, '毛线手套', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (588, '防晒手套', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (589, '男士腰带/礼盒', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (590, '女士腰带/礼盒', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (591, '钥匙扣', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (592, '遮阳伞/雨伞', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (593, '口罩', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (594, '耳罩/耳包', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (595, '假领', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (596, '毛线/布面料', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (597, '领带/领结/领带夹', 59, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (598, '男表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (599, '瑞表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (600, '女表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (601, '国表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (602, '日韩表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (603, '欧美表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (604, '德表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (605, '儿童手表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (606, '智能手表', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (607, '闹钟', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (608, '座钟挂钟', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (609, '钟表配件', 60, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (610, '商务休闲鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (611, '正装鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (612, '休闲鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (613, '凉鞋/沙滩鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (614, '男靴', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (615, '功能鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (616, '拖鞋/人字拖', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (617, '雨鞋/雨靴', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (618, '传统布鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (619, '鞋配件', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (620, '帆布鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (621, '增高鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (622, '工装鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (623, '定制鞋', 61, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (624, '高跟鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (625, '单鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (626, '休闲鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (627, '凉鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (628, '女靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (629, '雪地靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (630, '拖鞋/人字拖', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (631, '踝靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (632, '筒靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (633, '帆布鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (634, '雨鞋/雨靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (635, '妈妈鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (636, '鞋配件', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (637, '特色鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (638, '鱼嘴鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (639, '布鞋/绣花鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (640, '马丁靴', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (641, '坡跟鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (642, '松糕鞋', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (643, '内增高', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (644, '防水台', 62, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (645, '婴幼奶粉', 63, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (646, '孕妈奶粉', 63, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (647, '益生菌/初乳', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (648, '米粉/菜粉', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (649, '果泥/果汁', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (650, 'DHA', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (651, '宝宝零食', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (652, '钙铁锌/维生素', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (653, '清火/开胃', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (654, '面条/粥', 64, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (655, '婴儿尿裤', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (656, '拉拉裤', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (657, '婴儿湿巾', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (658, '成人尿裤', 65, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (659, '奶瓶奶嘴', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (660, '吸奶器', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (661, '暖奶消毒', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (662, '儿童餐具', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (663, '水壶/水杯', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (664, '牙胶安抚', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (665, '围兜/防溅衣', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (666, '辅食料理机', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (667, '食物存储', 66, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (668, '宝宝护肤', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (669, '洗发沐浴', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (670, '奶瓶清洗', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (671, '驱蚊防晒', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (672, '理发器', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (673, '洗澡用具', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (674, '婴儿口腔清洁', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (675, '洗衣液/皂', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (676, '日常护理', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (677, '座便器', 67, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (678, '婴儿推车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (679, '餐椅摇椅', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (680, '婴儿床', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (681, '学步车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (682, '三轮车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (683, '自行车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (684, '电动车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (685, '扭扭车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (686, '滑板车', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (687, '婴儿床垫', 68, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (688, '婴儿外出服', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (689, '婴儿内衣', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (690, '婴儿礼盒', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (691, '婴儿鞋帽袜', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (692, '安全防护', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (693, '家居床品', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (694, '睡袋/抱被', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (695, '爬行垫', 69, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (696, '妈咪包/背婴带', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (697, '产后塑身', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (698, '文胸/内裤', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (699, '防辐射服', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (700, '孕妈装', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (701, '孕期营养', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (702, '孕妇护肤', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (703, '待产护理', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (704, '月子装', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (705, '防溢乳垫', 70, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (706, '套装', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (707, '上衣', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (708, '裤子', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (709, '裙子', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (710, '内衣/家居服', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (711, '羽绒服/棉服', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (712, '亲子装', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (713, '儿童配饰', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (714, '礼服/演出服', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (715, '运动鞋', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (716, '皮鞋/帆布鞋', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (717, '靴子', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (718, '凉鞋', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (719, '功能鞋', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (720, '户外/运动服', 71, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (721, '提篮式', 72, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (722, '安全座椅', 72, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (723, '增高垫', 72, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (724, '钱包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (725, '手拿包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (726, '单肩包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (727, '双肩包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (728, '手提包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (729, '斜挎包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (730, '钥匙包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (731, '卡包/零钱包', 73, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (732, '男士钱包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (733, '男士手包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (734, '卡包名片夹', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (735, '商务公文包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (736, '双肩包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (737, '单肩/斜挎包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (738, '钥匙包', 74, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (739, '电脑包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (740, '拉杆箱', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (741, '旅行包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (742, '旅行配件', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (743, '休闲运动包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (744, '拉杆包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (745, '登山包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (746, '妈咪包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (747, '书包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (748, '相机包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (749, '腰包/胸包', 75, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (750, '火机烟具', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (751, '礼品文具', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (752, '军刀军具', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (753, '收藏品', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (754, '工艺礼品', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (755, '创意礼品', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (756, '礼盒礼券', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (757, '鲜花绿植', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (758, '婚庆节庆', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (759, '京东卡', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (760, '美妆礼品', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (761, '礼品定制', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (762, '京东福卡', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (763, '古董文玩', 76, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (764, '箱包', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (765, '钱包', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (766, '服饰', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (767, '腰带', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (768, '太阳镜/眼镜框', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (769, '配件', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (770, '鞋靴', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (771, '饰品', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (772, '名品腕表', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (773, '高档化妆品', 77, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (774, '婚嫁首饰', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (775, '婚纱摄影', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (776, '婚纱礼服', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (777, '婚庆服务', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (778, '婚庆礼品/用品', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (779, '婚宴', 78, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (780, '饼干蛋糕', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (781, '糖果/巧克力', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (782, '休闲零食', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (783, '冲调饮品', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (784, '粮油调味', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (785, '牛奶', 79, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (786, '其他特产', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (787, '新疆', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (788, '北京', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (789, '山西', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (790, '内蒙古', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (791, '福建', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (792, '湖南', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (793, '四川', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (794, '云南', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (795, '东北', 80, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (796, '休闲零食', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (797, '坚果炒货', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (798, '肉干肉脯', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (799, '蜜饯果干', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (800, '糖果/巧克力', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (801, '饼干蛋糕', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (802, '无糖食品', 81, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (803, '米面杂粮', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (804, '食用油', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (805, '调味品', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (806, '南北干货', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (807, '方便食品', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (808, '有机食品', 82, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (809, '饮用水', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (810, '饮料', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (811, '牛奶乳品', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (812, '咖啡/奶茶', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (813, '冲饮谷物', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (814, '蜂蜜/柚子茶', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (815, '成人奶粉', 83, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (816, '月饼', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (817, '大闸蟹', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (818, '粽子', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (819, '卡券', 84, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (820, '铁观音', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (821, '普洱', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (822, '龙井', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (823, '绿茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (824, '红茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (825, '乌龙茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (826, '花草茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (827, '花果茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (828, '养生茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (829, '黑茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (830, '白茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (831, '其它茶', 85, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (832, '项链', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (833, '手链/脚链', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (834, '戒指', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (835, '耳饰', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (836, '毛衣链', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (837, '发饰/发卡', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (838, '胸针', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (839, '饰品配件', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (840, '婚庆饰品', 86, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (841, '黄金吊坠', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (842, '黄金项链', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (843, '黄金转运珠', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (844, '黄金手镯/手链/脚链', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (845, '黄金耳饰', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (846, '黄金戒指', 87, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (847, 'K金吊坠', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (848, 'K金项链', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (849, 'K金手镯/手链/脚链', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (850, 'K金戒指', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (851, 'K金耳饰', 88, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (852, '投资金', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (853, '投资银', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (854, '投资收藏', 89, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (855, '银吊坠/项链', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (856, '银手镯/手链/脚链', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (857, '银戒指', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (858, '银耳饰', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (859, '足银手镯', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (860, '宝宝银饰', 90, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (861, '裸钻', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (862, '钻戒', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (863, '钻石项链/吊坠', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (864, '钻石耳饰', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (865, '钻石手镯/手链', 91, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (866, '项链/吊坠', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (867, '手镯/手串', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (868, '戒指', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (869, '耳饰', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (870, '挂件/摆件/把件', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (871, '玉石孤品', 92, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (872, '项链/吊坠', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (873, '耳饰', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (874, '手镯/手链/脚链', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (875, '戒指', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (876, '头饰/胸针', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (877, '摆件/挂件', 93, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (878, '琥珀/蜜蜡', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (879, '碧玺', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (880, '红宝石/蓝宝石', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (881, '坦桑石', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (882, '珊瑚', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (883, '祖母绿', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (884, '葡萄石', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (885, '其他天然宝石', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (886, '项链/吊坠', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (887, '耳饰', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (888, '手镯/手链', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (889, '戒指', 94, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (890, '铂金项链/吊坠', 95, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (891, '铂金手镯/手链/脚链', 95, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (892, '铂金戒指', 95, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (893, '铂金耳饰', 95, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (894, '小叶紫檀', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (895, '黄花梨', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (896, '沉香木', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (897, '金丝楠', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (898, '菩提', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (899, '其他', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (900, '橄榄核/核桃', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (901, '檀香', 96, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (902, '珍珠项链', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (903, '珍珠吊坠', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (904, '珍珠耳饰', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (905, '珍珠手链', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (906, '珍珠戒指', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (907, '珍珠胸针', 97, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (908, '机油', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (909, '正时皮带', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (910, '添加剂', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (911, '汽车喇叭', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (912, '防冻液', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (913, '汽车玻璃', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (914, '滤清器', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (915, '火花塞', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (916, '减震器', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (917, '柴机油/辅助油', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (918, '雨刷', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (919, '车灯', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (920, '后视镜', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (921, '轮胎', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (922, '轮毂', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (923, '刹车片/盘', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (924, '维修配件', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (925, '蓄电池', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (926, '底盘装甲/护板', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (927, '贴膜', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (928, '汽修工具', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (929, '改装配件', 98, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (930, '导航仪', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (931, '安全预警仪', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (932, '行车记录仪', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (933, '倒车雷达', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (934, '蓝牙设备', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (935, '车载影音', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (936, '净化器', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (937, '电源', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (938, '智能驾驶', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (939, '车载电台', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (940, '车载电器配件', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (941, '吸尘器', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (942, '智能车机', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (943, '冰箱', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (944, '汽车音响', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (945, '车载生活电器', 99, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (946, '车蜡', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (947, '补漆笔', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (948, '玻璃水', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (949, '清洁剂', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (950, '洗车工具', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (951, '镀晶镀膜', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (952, '打蜡机', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (953, '洗车配件', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (954, '洗车机', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (955, '洗车水枪', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (956, '毛巾掸子', 100, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (957, '脚垫', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (958, '座垫', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (959, '座套', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (960, '后备箱垫', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (961, '头枕腰靠', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (962, '方向盘套', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (963, '香水', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (964, '空气净化', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (965, '挂件摆件', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (966, '功能小件', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (967, '车身装饰件', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (968, '车衣', 101, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (969, '安全座椅', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (970, '胎压监测', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (971, '防盗设备', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (972, '应急救援', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (973, '保温箱', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (974, '地锁', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (975, '摩托车', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (976, '充气泵', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (977, '储物箱', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (978, '自驾野营', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (979, '摩托车装备', 102, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (980, '清洗美容', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (981, '功能升级', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (982, '保养维修', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (983, '油卡充值', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (984, '车险', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (985, '加油卡', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (986, 'ETC', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (987, '驾驶培训', 103, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (988, '赛事服装', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (989, '赛事用品', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (990, '制动系统', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (991, '悬挂系统', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (992, '进气系统', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (993, '排气系统', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (994, '电子管理', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (995, '车身强化', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (996, '赛事座椅', 104, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (997, '跑步鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (998, '休闲鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (999, '篮球鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1000, '板鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1001, '帆布鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1002, '足球鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1003, '乒羽网鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1004, '专项运动鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1005, '训练鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1006, '拖鞋', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1007, '运动包', 105, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1008, '羽绒服', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1009, '棉服', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1010, '运动裤', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1011, '夹克/风衣', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1012, '卫衣/套头衫', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1013, 'T恤', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1014, '套装', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1015, '乒羽网服', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1016, '健身服', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1017, '运动背心', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1018, '毛衫/线衫', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1019, '运动配饰', 106, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1020, '折叠车', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1021, '山地车/公路车', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1022, '电动车', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1023, '其他整车', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1024, '骑行服', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1025, '骑行装备', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1026, '平衡车', 107, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1027, '鱼竿鱼线', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1028, '浮漂鱼饵', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1029, '钓鱼桌椅', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1030, '钓鱼配件', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1031, '钓箱鱼包', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1032, '其它', 108, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1033, '泳镜', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1034, '泳帽', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1035, '游泳包防水包', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1036, '女士泳衣', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1037, '男士泳衣', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1038, '比基尼', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1039, '其它', 109, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1040, '冲锋衣裤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1041, '速干衣裤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1042, '滑雪服', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1043, '羽绒服/棉服', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1044, '休闲衣裤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1045, '抓绒衣裤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1046, '软壳衣裤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1047, 'T恤', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1048, '户外风衣', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1049, '功能内衣', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1050, '军迷服饰', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1051, '登山鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1052, '雪地靴', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1053, '徒步鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1054, '越野跑鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1055, '休闲鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1056, '工装鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1057, '溯溪鞋', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1058, '沙滩/凉拖', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1059, '户外袜', 110, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1060, '帐篷/垫子', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1061, '睡袋/吊床', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1062, '登山攀岩', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1063, '户外配饰', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1064, '背包', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1065, '户外照明', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1066, '户外仪表', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1067, '户外工具', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1068, '望远镜', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1069, '旅游用品', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1070, '便携桌椅床', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1071, '野餐烧烤', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1072, '军迷用品', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1073, '救援装备', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1074, '滑雪装备', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1075, '极限户外', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1076, '冲浪潜水', 111, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1077, '综合训练器', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1078, '其他大型器械', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1079, '哑铃', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1080, '仰卧板/收腹机', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1081, '其他中小型器材', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1082, '瑜伽舞蹈', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1083, '甩脂机', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1084, '踏步机', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1085, '武术搏击', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1086, '健身车/动感单车', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1087, '跑步机', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1088, '运动护具', 112, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1089, '羽毛球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1090, '乒乓球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1091, '篮球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1092, '足球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1093, '网球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1094, '排球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1095, '高尔夫', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1096, '台球', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1097, '棋牌麻将', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1098, '轮滑滑板', 113, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_category3` VALUES (1099, '其他', 113, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_dic
-- ----------------------------
DROP TABLE IF EXISTS `base_dic`;
CREATE TABLE `base_dic`  (
  `dic_code` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '编号',
  `dic_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '编码名称',
  `parent_code` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '父编号',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建日期',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改日期',
  PRIMARY KEY (`dic_code`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_dic
-- ----------------------------
INSERT INTO `base_dic` VALUES ('07', '退单状态', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0701', '商家审核中', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0702', '商家审核通过', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0703', '商家审核未通过', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0704', '买家已发货', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0705', '退单完成', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('0706', '退单失败', '07', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('10', '单据状态', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1001', '未支付', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1002', '已支付', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1003', '已取消', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1004', '已完成', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1005', '退款中', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1006', '退款完成', '10', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('11', '支付状态', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1101', '支付宝', '11', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1102', '微信', '11', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1103', '银联', '11', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('12', '评价', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1201', '好评', '12', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1202', '中评', '12', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1203', '差评', '12', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1204', '自动', '12', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('13', '退货原因', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1301', '质量问题', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1302', '商品描述与实际描述不一致', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1303', '缺货', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1304', '号码不合适', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1305', '拍错', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1306', '不想买了', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1307', '其他', '13', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('14', '购物券状态', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1401', '未使用', '14', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1402', '使用中', '14', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1403', '已使用', '14', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('15', '退款类型', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1501', '仅退款', '15', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1502', '退货退款', '15', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('16', '支付状态', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1601', '未支付', '16', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1602', '已支付', '16', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('1603', '取消支付', '16', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('24', '来源类型', '', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('2401', '用户查询', '24', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('2402', '商品推广', '24', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('2403', '智能推荐', '24', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('2404', '促销活动', '24', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('31', '活动类型', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3101', '满额减价', '31', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3102', '满量打折', '31', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3103', '直接折扣', '31', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('32', '购物券类型', '', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3201', '满额减价', '32', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3202', '满量打折', '32', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3203', '代金券', '32', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('33', '优惠券范围', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3301', '品类券', '33', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3302', '品牌券', '33', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3303', '单品(spu)', '33', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('35', '付款方式', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3501', '在线支付', '35', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('3502', '货到付款', '35', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('41', '营销类型', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4101', '固定', '41', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4102', '算法', '41', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('42', '坑位类型', '', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4201', 'banner', '', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4202', '宫格', '42', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4203', '列表', '42', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_dic` VALUES ('4204', '瀑布', '42', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_frontend_param
-- ----------------------------
DROP TABLE IF EXISTS `base_frontend_param`;
CREATE TABLE `base_frontend_param`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `code` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '属性名称',
  `delete_id` bigint NULL DEFAULT NULL COMMENT '分类id',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '前端数据保护表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_frontend_param
-- ----------------------------

-- ----------------------------
-- Table structure for base_province
-- ----------------------------
DROP TABLE IF EXISTS `base_province`;
CREATE TABLE `base_province`  (
  `id` bigint NOT NULL COMMENT 'id',
  `name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '省份名称',
  `region_id` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '地区id',
  `area_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '地区编码',
  `iso_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '旧版国际标准地区编码，供可视化使用',
  `iso_3166_2` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '新版国际标准地区编码，供可视化使用',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '省份表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_province
-- ----------------------------
INSERT INTO `base_province` VALUES (1, '北京', '1', '110000', 'CN-11', 'CN-BJ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (2, '天津', '1', '120000', 'CN-12', 'CN-TJ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (3, '山西', '1', '140000', 'CN-14', 'CN-SX', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (4, '内蒙古', '1', '150000', 'CN-15', 'CN-NM', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (5, '河北', '1', '130000', 'CN-13', 'CN-HE', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (6, '上海', '2', '310000', 'CN-31', 'CN-SH', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (7, '江苏', '2', '320000', 'CN-32', 'CN-JS', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (8, '浙江', '2', '330000', 'CN-33', 'CN-ZJ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (9, '安徽', '2', '340000', 'CN-34', 'CN-AH', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (10, '福建', '2', '350000', 'CN-35', 'CN-FJ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (11, '江西', '2', '360000', 'CN-36', 'CN-JX', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (12, '山东', '2', '370000', 'CN-37', 'CN-SD', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (13, '重庆', '6', '500000', 'CN-50', 'CN-CQ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (14, '台湾', '2', '710000', 'CN-71', 'CN-TW', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (15, '黑龙江', '3', '230000', 'CN-23', 'CN-HL', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (16, '吉林', '3', '220000', 'CN-22', 'CN-JL', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (17, '辽宁', '3', '210000', 'CN-21', 'CN-LN', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (18, '陕西', '7', '610000', 'CN-61', 'CN-SN', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (19, '甘肃', '7', '620000', 'CN-62', 'CN-GS', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (20, '青海', '7', '630000', 'CN-63', 'CN-QH', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (21, '宁夏', '7', '640000', 'CN-64', 'CN-NX', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (22, '新疆', '7', '650000', 'CN-65', 'CN-XJ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (23, '河南', '4', '410000', 'CN-41', 'CN-HA', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (24, '湖北', '4', '420000', 'CN-42', 'CN-HB', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (25, '湖南', '4', '430000', 'CN-43', 'CN-HN', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (26, '广东', '5', '440000', 'CN-44', 'CN-GD', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (27, '广西', '5', '450000', 'CN-45', 'CN-GX', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (28, '海南', '5', '460000', 'CN-46', 'CN-HI', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (29, '香港', '5', '810000', 'CN-91', 'CN-HK', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (30, '澳门', '5', '820000', 'CN-92', 'CN-MO', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (31, '四川', '6', '510000', 'CN-51', 'CN-SC', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (32, '贵州', '6', '520000', 'CN-52', 'CN-GZ', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (33, '云南', '6', '530000', 'CN-53', 'CN-YN', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_province` VALUES (34, '西藏', '6', '540000', 'CN-54', 'CN-XZ', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for base_region
-- ----------------------------
DROP TABLE IF EXISTS `base_region`;
CREATE TABLE `base_region`  (
  `id` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '地区id',
  `region_name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '地区名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '地区表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_region
-- ----------------------------
INSERT INTO `base_region` VALUES ('1', '华北', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('2', '华东', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('3', '东北', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('4', '华中', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('5', '华南', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('6', '西南', '2020-05-01 11:20:40', NULL);
INSERT INTO `base_region` VALUES ('7', '西北', '2020-05-01 11:20:40', NULL);

-- ----------------------------
-- Table structure for base_sale_attr
-- ----------------------------
DROP TABLE IF EXISTS `base_sale_attr`;
CREATE TABLE `base_sale_attr`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '销售属性名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '基本销售属性表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_sale_attr
-- ----------------------------
INSERT INTO `base_sale_attr` VALUES (1, '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_sale_attr` VALUES (2, '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_sale_attr` VALUES (3, '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_sale_attr` VALUES (4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_sale_attr` VALUES (5, '处理器或显卡', NULL, NULL);

-- ----------------------------
-- Table structure for base_trademark
-- ----------------------------
DROP TABLE IF EXISTS `base_trademark`;
CREATE TABLE `base_trademark`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `tm_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '品牌名称',
  `logo_url` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '品牌logo的图片路径',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '品牌表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of base_trademark
-- ----------------------------
INSERT INTO `base_trademark` VALUES (1, 'Redmi', NULL, '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (2, '苹果', '/static/default.jpg2323', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (3, '联想', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (4, 'TCL', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (5, '小米', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (6, '长粒香', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (7, '金沙河', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (8, '索芙特', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (9, 'CAREMiLLE', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (10, '欧莱雅', '/static/default.jpg', '2021-12-14 00:00:00', NULL);
INSERT INTO `base_trademark` VALUES (11, '香奈儿', '/static/default.jpg', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for cart_info
-- ----------------------------
DROP TABLE IF EXISTS `cart_info`;
CREATE TABLE `cart_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `user_id` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `cart_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '放入购物车时价格',
  `sku_num` int NULL DEFAULT NULL COMMENT '数量',
  `img_url` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '商品图片地址',
  `sku_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'sku名称 (冗余)',
  `is_checked` int NULL DEFAULT NULL COMMENT '是否被选中',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  `is_ordered` bigint NULL DEFAULT NULL COMMENT '是否已经下单',
  `order_time` datetime NULL DEFAULT NULL COMMENT '下单时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_uid`(`user_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '购物车表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of cart_info
-- ----------------------------

-- ----------------------------
-- Table structure for cms_banner
-- ----------------------------
DROP TABLE IF EXISTS `cms_banner`;
CREATE TABLE `cms_banner`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `title` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT '' COMMENT '标题',
  `image_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT '' COMMENT '图片地址',
  `link_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT '' COMMENT '链接地址',
  `sort` int UNSIGNED NOT NULL DEFAULT 0 COMMENT '排序',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '首页banner表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of cms_banner
-- ----------------------------

-- ----------------------------
-- Table structure for comment_info
-- ----------------------------
DROP TABLE IF EXISTS `comment_info`;
CREATE TABLE `comment_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `nick_name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户昵称',
  `head_img` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `spu_id` bigint NULL DEFAULT NULL COMMENT 'spuid',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单编号',
  `appraise` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '评价 1 好评 2 中评 3 差评',
  `comment_txt` varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '评价内容',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '商品评论表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of comment_info
-- ----------------------------

-- ----------------------------
-- Table structure for coupon_info
-- ----------------------------
DROP TABLE IF EXISTS `coupon_info`;
CREATE TABLE `coupon_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '购物券编号',
  `coupon_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '购物券名称',
  `coupon_type` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '购物券类型 1 现金券 2 折扣券 3 满减券 4 满件打折券',
  `condition_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '满额数',
  `condition_num` bigint NULL DEFAULT NULL COMMENT '满件数',
  `activity_id` bigint NULL DEFAULT NULL COMMENT '活动编号',
  `benefit_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '减免金额',
  `benefit_discount` decimal(10, 2) NULL DEFAULT NULL COMMENT '折扣',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `range_type` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '范围类型 1、商品(spuid) 2、品类(三级分类id) 3、品牌',
  `limit_num` int NOT NULL DEFAULT 0 COMMENT '最多领用次数',
  `taken_count` int NOT NULL DEFAULT 0 COMMENT '已领用次数',
  `start_time` datetime NULL DEFAULT NULL COMMENT '可以领取的开始时间',
  `end_time` datetime NULL DEFAULT NULL COMMENT '可以领取的结束时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  `expire_time` datetime NULL DEFAULT NULL COMMENT '过期时间',
  `range_desc` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '范围描述',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '优惠券信息表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of coupon_info
-- ----------------------------
INSERT INTO `coupon_info` VALUES (1, '口红品类券满99减30', '3201', 99.00, NULL, NULL, 30.00, NULL, '2022-06-06 00:00:00', '3301', 100, 0, '2022-02-13 01:15:41', '2023-12-13 01:15:58', NULL, '2023-12-13 01:15:58', NULL);
INSERT INTO `coupon_info` VALUES (2, '口红品类券满199减70', '3201', 199.00, NULL, NULL, 70.00, NULL, '2022-05-20 00:00:00', '3301', 100, 0, '2022-02-13 01:15:41', '2023-12-13 01:15:58', NULL, '2023-12-13 01:15:58', NULL);
INSERT INTO `coupon_info` VALUES (3, '口红品类券满499减100', '3201', 499.00, NULL, NULL, 100.00, NULL, '2022-06-02 00:00:00', '3301', 100, 0, '2022-02-13 01:15:41', '2023-12-13 01:15:58', NULL, '2023-12-13 01:15:58', NULL);
INSERT INTO `coupon_info` VALUES (4, '金沙河面条买二送一', '3202', NULL, 2, NULL, 0.00, 5.00, '2022-06-02 00:00:00', '3302', 100, 0, '2022-02-13 01:15:41', '2023-12-13 01:15:58', NULL, '2023-12-13 01:15:58', NULL);
INSERT INTO `coupon_info` VALUES (5, 'iPhone 代金券', '3203', NULL, NULL, NULL, 300.00, NULL, '2022-06-02 00:00:00', '3303', 100, 0, '2022-02-13 01:15:41', '2023-12-13 01:15:58', NULL, NULL, NULL);

-- ----------------------------
-- Table structure for coupon_range
-- ----------------------------
DROP TABLE IF EXISTS `coupon_range`;
CREATE TABLE `coupon_range`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '购物券编号',
  `coupon_id` bigint NOT NULL DEFAULT 0 COMMENT '优惠券id',
  `range_type` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT '' COMMENT '范围类型 1、商品(spuid) 2、品类(三级分类id) 3、品牌',
  `range_id` bigint NOT NULL DEFAULT 0,
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '优惠券范围表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of coupon_range
-- ----------------------------
INSERT INTO `coupon_range` VALUES (1, 1, '3301', 477, '2021-12-14 00:00:00', NULL);
INSERT INTO `coupon_range` VALUES (2, 2, '3301', 477, '2021-12-14 00:00:00', NULL);
INSERT INTO `coupon_range` VALUES (3, 3, '3301', 477, '2021-12-14 00:00:00', NULL);
INSERT INTO `coupon_range` VALUES (4, 4, '3302', 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `coupon_range` VALUES (5, 5, '3303', 0, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for coupon_use
-- ----------------------------
DROP TABLE IF EXISTS `coupon_use`;
CREATE TABLE `coupon_use`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `coupon_id` bigint NULL DEFAULT NULL COMMENT '购物券ID',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户ID',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单ID',
  `coupon_status` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '购物券状态（1：未使用 2：已使用）',
  `get_time` datetime NULL DEFAULT NULL COMMENT '获取时间',
  `using_time` datetime NULL DEFAULT NULL COMMENT '使用时间',
  `used_time` datetime NULL DEFAULT NULL COMMENT '支付时间',
  `expire_time` datetime NULL DEFAULT NULL COMMENT '过期时间',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '优惠券领用表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of coupon_use
-- ----------------------------

-- ----------------------------
-- Table structure for favor_info
-- ----------------------------
DROP TABLE IF EXISTS `favor_info`;
CREATE TABLE `favor_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `spu_id` bigint NULL DEFAULT NULL COMMENT 'spuid',
  `is_cancel` varchar(1) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '是否已取消 0 正常 1 已取消',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '商品收藏表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of favor_info
-- ----------------------------

-- ----------------------------
-- Table structure for financial_sku_cost
-- ----------------------------
DROP TABLE IF EXISTS `financial_sku_cost`;
CREATE TABLE `financial_sku_cost`  (
  `id` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `sku_name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'sku名称',
  `busi_date` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '业务日期',
  `is_lastest` varchar(2) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '是否最近',
  `sku_cost` decimal(16, 2) NULL DEFAULT NULL COMMENT '商品结算成本',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of financial_sku_cost
-- ----------------------------

-- ----------------------------
-- Table structure for order_detail
-- ----------------------------
DROP TABLE IF EXISTS `order_detail`;
CREATE TABLE `order_detail`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `sku_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'sku名称（冗余)',
  `img_url` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片链接（冗余)',
  `order_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '购买价格(下单时sku价格）',
  `sku_num` bigint NULL DEFAULT NULL COMMENT '购买个数',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `split_total_amount` decimal(16, 2) NULL DEFAULT NULL,
  `split_activity_amount` decimal(16, 2) NULL DEFAULT NULL,
  `split_coupon_amount` decimal(16, 2) NULL DEFAULT NULL,
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '订单明细表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_detail
-- ----------------------------

-- ----------------------------
-- Table structure for order_detail_activity
-- ----------------------------
DROP TABLE IF EXISTS `order_detail_activity`;
CREATE TABLE `order_detail_activity`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `order_detail_id` bigint NULL DEFAULT NULL COMMENT '订单明细id',
  `activity_id` bigint NULL DEFAULT NULL COMMENT '活动id',
  `activity_rule_id` bigint NULL DEFAULT NULL COMMENT '活动规则id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '订单明细活动关联表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_detail_activity
-- ----------------------------

-- ----------------------------
-- Table structure for order_detail_coupon
-- ----------------------------
DROP TABLE IF EXISTS `order_detail_coupon`;
CREATE TABLE `order_detail_coupon`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `order_detail_id` bigint NULL DEFAULT NULL COMMENT '订单明细id',
  `coupon_id` bigint NULL DEFAULT NULL COMMENT '购物券id',
  `coupon_use_id` bigint NULL DEFAULT NULL COMMENT '购物券领用id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '订单明细优惠券关联表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_detail_coupon
-- ----------------------------

-- ----------------------------
-- Table structure for order_info
-- ----------------------------
DROP TABLE IF EXISTS `order_info`;
CREATE TABLE `order_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `consignee` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '收货人',
  `consignee_tel` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '收件人电话',
  `total_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '总金额',
  `order_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单状态',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `payment_way` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '付款方式',
  `delivery_address` varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '送货地址',
  `order_comment` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单备注',
  `out_trade_no` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单交易编号（第三方支付用)',
  `trade_body` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单描述(第三方支付用)',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '操作时间',
  `expire_time` datetime NULL DEFAULT NULL COMMENT '失效时间',
  `process_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '进度状态',
  `tracking_no` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '物流单编号',
  `parent_order_id` bigint NULL DEFAULT NULL COMMENT '父订单编号',
  `img_url` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片链接',
  `province_id` int NULL DEFAULT NULL COMMENT '省份id',
  `activity_reduce_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '活动减免金额',
  `coupon_reduce_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '优惠券减免金额',
  `original_total_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '原始总金额',
  `feight_fee` decimal(16, 2) NULL DEFAULT NULL COMMENT '运费金额',
  `feight_fee_reduce` decimal(16, 2) NULL DEFAULT NULL COMMENT '运费减免金额',
  `refundable_time` datetime NULL DEFAULT NULL COMMENT '可退款时间（签收后30天）',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_uid_status`(`order_status` ASC, `user_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '订单表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_info
-- ----------------------------

-- ----------------------------
-- Table structure for order_refund_info
-- ----------------------------
DROP TABLE IF EXISTS `order_refund_info`;
CREATE TABLE `order_refund_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `refund_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '退款类型',
  `refund_num` bigint NULL DEFAULT NULL COMMENT '退货件数',
  `refund_amount` decimal(16, 2) NULL DEFAULT NULL COMMENT '退款金额',
  `refund_reason_type` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '原因类型',
  `refund_reason_txt` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '原因内容',
  `refund_status` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '退款状态（0：待审批 1：已退款）',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '退单表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_refund_info
-- ----------------------------

-- ----------------------------
-- Table structure for order_status_log
-- ----------------------------
DROP TABLE IF EXISTS `order_status_log`;
CREATE TABLE `order_status_log`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `order_status` varchar(11) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单状态',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '订单状态流水表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of order_status_log
-- ----------------------------

-- ----------------------------
-- Table structure for payment_info
-- ----------------------------
DROP TABLE IF EXISTS `payment_info`;
CREATE TABLE `payment_info`  (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '编号',
  `out_trade_no` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '对外业务编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `payment_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '支付类型（微信 支付宝）',
  `trade_no` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '交易编号',
  `total_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '支付金额',
  `subject` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '交易内容',
  `payment_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '支付状态',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `callback_time` datetime NULL DEFAULT NULL COMMENT '回调时间',
  `callback_content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '回调信息',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '支付信息表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of payment_info
-- ----------------------------

-- ----------------------------
-- Table structure for promotion_pos
-- ----------------------------
DROP TABLE IF EXISTS `promotion_pos`;
CREATE TABLE `promotion_pos`  (
  `id` bigint NOT NULL COMMENT '营销坑位id',
  `pos_location` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '营销坑位位置',
  `pos_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '营销坑位类型：banner,宫格,列表, 瀑布',
  `promotion_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '营销类型：算法、固定、搜索',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '营销坑位表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of promotion_pos
-- ----------------------------
INSERT INTO `promotion_pos` VALUES (1, '首页banner', '4201', '4101', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (2, '首页下方瀑布推荐', '4204', '4102', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (3, '搜索页排行列表', '4203', '4102', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (4, '详情页下方9宫格', '4202', '4102', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (5, '购物车底部瀑布', '4104', '4102', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (6, '我的页底部瀑布', '4104', '4102', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (7, '新品精选-新发现瀑布', '4204', '4101', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (8, '双十一活动页瀑布', '4204', '4101', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (9, '双十一活动banner', '4201', '4101', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_pos` VALUES (10, '商品搜索结果列表瀑布', '4204', '4103', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for promotion_refer
-- ----------------------------
DROP TABLE IF EXISTS `promotion_refer`;
CREATE TABLE `promotion_refer`  (
  `id` bigint NOT NULL COMMENT '外部营销渠道id',
  `refer_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '外部营销渠道名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '营销渠道表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of promotion_refer
-- ----------------------------
INSERT INTO `promotion_refer` VALUES (1, '百度推广', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_refer` VALUES (2, '小红书软文', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_refer` VALUES (3, '手机推送消息', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_refer` VALUES (4, '微信公众号', '2021-12-14 00:00:00', NULL);
INSERT INTO `promotion_refer` VALUES (5, '微博推广', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for refund_payment
-- ----------------------------
DROP TABLE IF EXISTS `refund_payment`;
CREATE TABLE `refund_payment`  (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '编号',
  `out_trade_no` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '对外业务编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `payment_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '支付类型（微信 支付宝）',
  `trade_no` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '交易编号',
  `total_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '退款金额',
  `subject` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '交易内容',
  `refund_status` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '退款状态',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `callback_time` datetime NULL DEFAULT NULL COMMENT '回调时间',
  `callback_content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '回调信息',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_out_trade_no`(`out_trade_no` ASC) USING BTREE,
  INDEX `idx_order_id`(`order_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '退款支付表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of refund_payment
-- ----------------------------

-- ----------------------------
-- Table structure for seckill_goods
-- ----------------------------
DROP TABLE IF EXISTS `seckill_goods`;
CREATE TABLE `seckill_goods`  (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `spu_id` bigint NULL DEFAULT NULL COMMENT 'spu_id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `sku_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '标题',
  `sku_default_img` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '商品图片',
  `price` decimal(10, 2) NULL DEFAULT NULL COMMENT '原价格',
  `cost_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '秒杀价格',
  `create_time` datetime NULL DEFAULT NULL COMMENT '添加日期',
  `check_time` datetime NULL DEFAULT NULL COMMENT '审核日期',
  `status` varchar(1) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '审核状态',
  `start_time` datetime NULL DEFAULT NULL COMMENT '开始时间',
  `end_time` datetime NULL DEFAULT NULL COMMENT '结束时间',
  `num` int NULL DEFAULT NULL COMMENT '秒杀商品数',
  `stock_count` int NULL DEFAULT NULL COMMENT '剩余库存数',
  `sku_desc` varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '描述',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of seckill_goods
-- ----------------------------

-- ----------------------------
-- Table structure for sku_attr_value
-- ----------------------------
DROP TABLE IF EXISTS `sku_attr_value`;
CREATE TABLE `sku_attr_value`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `attr_id` bigint NULL DEFAULT NULL COMMENT '平台属性id（冗余)',
  `value_id` bigint NULL DEFAULT NULL COMMENT '平台属性值id',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `attr_name` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '平台属性名称',
  `value_name` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '平台属性值名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'sku平台属性值关联表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of sku_attr_value
-- ----------------------------
INSERT INTO `sku_attr_value` VALUES (1, 106, 176, 1, '手机一级1', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (2, 107, 177, 1, '二级手机2', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (3, 23, 83, 1, '运行内存', '8G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (4, 24, 82, 1, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (5, 106, 176, 2, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (6, 107, 177, 2, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (7, 23, 83, 2, '运行内存', '8G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (8, 24, 166, 2, '机身内存', '256G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (9, 106, 176, 3, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (10, 107, 177, 3, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (11, 23, 83, 3, '运行内存', '8G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (12, 24, 82, 3, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (13, 106, 176, 4, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (14, 107, 177, 4, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (15, 23, 14, 4, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (16, 24, 82, 4, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (17, 106, 176, 5, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (18, 107, 177, 5, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (19, 23, 14, 5, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (20, 24, 82, 5, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (21, 106, 176, 6, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (22, 107, 177, 6, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (23, 23, 83, 6, '运行内存', '8G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (24, 24, 82, 6, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (25, 106, 176, 7, '手机一级', '安卓手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (26, 107, 177, 7, '二级手机', '小米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (27, 23, 83, 7, '运行内存', '8G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (28, 24, 82, 7, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (29, 106, 175, 8, '手机一级', '苹果手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (30, 107, 179, 8, '二级手机', '苹果', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (31, 23, 14, 8, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (32, 24, 82, 8, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (33, 106, 175, 9, '手机一级', '苹果手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (34, 107, 179, 9, '二级手机', '苹果', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (35, 23, 14, 9, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (36, 24, 82, 9, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (37, 106, 175, 10, '手机一级', '苹果手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (38, 107, 179, 10, '二级手机', '苹果', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (39, 23, 14, 10, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (40, 24, 82, 10, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (41, 106, 175, 11, '手机一级', '苹果手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (42, 107, 179, 11, '二级手机', '苹果', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (43, 23, 14, 11, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (44, 24, 82, 11, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (45, 106, 175, 12, '手机一级', '苹果手机', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (46, 107, 179, 12, '二级手机', '苹果', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (47, 23, 14, 12, '运行内存', '4G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (48, 24, 82, 12, '机身内存', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (49, 25, 20, 13, '处理器', 'i9', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (50, 26, 22, 13, '显卡', 'RTX3060', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (51, 66, 84, 13, '硬盘', '512GB', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (52, 67, 86, 13, '内存', '16G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (53, 25, 20, 14, '处理器', 'i9', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (54, 26, 21, 14, '显卡', 'RTX3070Ti', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (55, 66, 84, 14, '硬盘', '512GB', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (56, 67, 86, 14, '内存', '16G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (57, 25, 19, 15, '处理器', 'i7', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (58, 26, 22, 15, '显卡', 'RTX3060', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (59, 66, 84, 15, '硬盘', '512GB', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (60, 67, 82, 15, '内存', '16G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (61, 25, 19, 16, '处理器', 'i7', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (62, 26, 22, 16, '显卡', 'RTX3060', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (63, 66, 85, 16, '硬盘', '512GB+2T', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (64, 67, 82, 16, '内存', '16G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (65, 89, 136, 17, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (66, 96, 195, 17, '屏幕尺寸', '60-70', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (67, 97, 147, 17, '分辨率', '4K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (68, 98, 149, 17, '观看距离', '3.5m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (69, 89, 136, 17, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (70, 89, 136, 18, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (71, 96, 144, 18, '屏幕尺寸', '70-75', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (72, 97, 146, 18, '分辨率', '8K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (73, 98, 149, 18, '观看距离', '3.5m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (74, 89, 136, 18, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (75, 89, 193, 19, '电视类型', '人工智能', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (76, 96, 145, 19, '屏幕尺寸', '75以上', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (77, 97, 148, 19, '分辨率', '12K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (78, 98, 149, 19, '观看距离', '3.5m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (79, 89, 136, 19, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (80, 89, 192, 20, '电视类型', '超薄', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (81, 96, 195, 20, '屏幕尺寸', '60-70', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (82, 97, 147, 20, '分辨率', '4K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (83, 98, 150, 20, '观看距离', '3m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (84, 89, 136, 20, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (85, 89, 191, 21, '电视类型', '曲面', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (86, 96, 144, 21, '屏幕尺寸', '70-75', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (87, 97, 147, 21, '分辨率', '4K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (88, 98, 150, 21, '观看距离', '3m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (89, 89, 136, 21, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (90, 108, 197, 22, '粮食调味', '大米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (91, 109, 201, 22, '是否有机', '非有机食品', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (92, 110, 203, 22, '分类', '米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (93, 108, 197, 23, '粮食调味', '大米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (94, 109, 201, 23, '是否有机', '非有机食品', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (95, 110, 203, 23, '分类', '米', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (96, 108, 198, 24, '粮食调味', '面食', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (97, 109, 200, 24, '是否有机', '有机食品', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (98, 110, 204, 24, '分类', '面', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (99, 108, 198, 25, '粮食调味', '面食', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (100, 109, 200, 25, '是否有机', '有机食品', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (101, 110, 204, 25, '分类', '面', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (102, 111, 206, 26, '功效', '滋润', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (103, 112, 210, 26, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (104, 113, 212, 26, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (105, 111, 207, 27, '功效', '保湿', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (106, 112, 210, 27, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (107, 113, 212, 27, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (108, 111, 208, 28, '功效', '防水', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (109, 112, 210, 28, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (110, 113, 212, 28, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (111, 111, 209, 29, '功效', '防晒', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (112, 112, 210, 29, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (113, 113, 212, 29, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (114, 111, 207, 30, '功效', '保湿', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (115, 112, 210, 30, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (116, 113, 212, 30, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (117, 111, 209, 31, '功效', '防晒', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (118, 112, 210, 31, '香水彩妆', '口红', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (119, 113, 212, 31, '面部护肤', '润唇膏', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (120, 114, 216, 32, '香调', '海洋香调', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (121, 114, 215, 33, '香调', '花果香调', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (122, 89, 136, 34, '电视类型', '4K超清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (123, 96, 194, 34, '屏幕尺寸', '60以下', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (124, 97, 147, 34, '分辨率', '4K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (125, 98, 150, 34, '观看距离', '3m', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (126, 89, 193, 35, '电视类型', '人工智能', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (127, 96, 195, 35, '屏幕尺寸', '60-70', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (128, 97, 147, 35, '分辨率', '4K超高清', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_attr_value` VALUES (129, 98, 150, 35, '观看距离', '3m', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for sku_image
-- ----------------------------
DROP TABLE IF EXISTS `sku_image`;
CREATE TABLE `sku_image`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `img_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片名称（冗余）',
  `img_url` varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片路径(冗余)',
  `spu_img_id` bigint NULL DEFAULT NULL COMMENT 'spu图片id',
  `is_default` varchar(4000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '是否默认',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '库存单元图片表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of sku_image
-- ----------------------------
INSERT INTO `sku_image` VALUES (1, 1, 'ead186426badb626.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAHpFuAACTenfIJWo734.jpg', 2, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (2, 1, 'b58ab2d79b859f39.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAcbl2AAFopp2WGBQ404.jpg', 3, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (3, 1, '0d93a071c839d890.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmATs5EAABriLbF9vE207.jpg', 4, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (4, 1, 'a7b1125239354d0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAeWopAAEtpBjP1VQ788.jpg', 5, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (5, 1, '6029cb2c2b2c7668.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAZJX2AAGmVo8Tz9s683.jpg', 6, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (6, 1, '2ff0882c9607e57c.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAIpgZAAIvrX6L9fo612.jpg', 1, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (7, 2, 'ead186426badb626.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAHpFuAACTenfIJWo734.jpg', 2, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (8, 2, 'b58ab2d79b859f39.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAcbl2AAFopp2WGBQ404.jpg', 3, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (9, 2, '0d93a071c839d890.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmATs5EAABriLbF9vE207.jpg', 4, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (10, 2, 'a7b1125239354d0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAeWopAAEtpBjP1VQ788.jpg', 5, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (11, 2, '6029cb2c2b2c7668.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAZJX2AAGmVo8Tz9s683.jpg', 6, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (12, 3, '2ff0882c9607e57c.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAIpgZAAIvrX6L9fo612.jpg', 1, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (13, 3, 'ead186426badb626.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAHpFuAACTenfIJWo734.jpg', 2, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (14, 3, 'b58ab2d79b859f39.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAcbl2AAFopp2WGBQ404.jpg', 3, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (15, 3, '0d93a071c839d890.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmATs5EAABriLbF9vE207.jpg', 4, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (16, 3, 'a7b1125239354d0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAeWopAAEtpBjP1VQ788.jpg', 5, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (17, 3, '6029cb2c2b2c7668.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAZJX2AAGmVo8Tz9s683.jpg', 6, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (18, 4, '2b78b6fdabfd2fbe.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 7, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (19, 4, '5d5c57ab443f5fbb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAbNFZAADi9nT5SuM524.jpg', 8, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (20, 4, 'cef3c55b8caad783.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAMq2nAAGtjwY17fA932.jpg', 9, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (21, 4, '6eaaecc91b9ad059.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAXjRKAAFpxKwrSvY886.jpg', 10, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (22, 4, 'b7bea7af48e935fd.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqARA19AAFuZ18W_ks439.jpg', 11, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (23, 5, '2b78b6fdabfd2fbe.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 7, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (24, 5, '5d5c57ab443f5fbb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAbNFZAADi9nT5SuM524.jpg', 8, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (25, 5, 'cef3c55b8caad783.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAMq2nAAGtjwY17fA932.jpg', 9, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (26, 5, '6eaaecc91b9ad059.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAXjRKAAFpxKwrSvY886.jpg', 10, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (27, 5, 'b7bea7af48e935fd.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqARA19AAFuZ18W_ks439.jpg', 11, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (28, 6, '2b78b6fdabfd2fbe.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 7, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (29, 6, '5d5c57ab443f5fbb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAbNFZAADi9nT5SuM524.jpg', 8, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (30, 6, 'cef3c55b8caad783.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAMq2nAAGtjwY17fA932.jpg', 9, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (31, 6, '6eaaecc91b9ad059.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAXjRKAAFpxKwrSvY886.jpg', 10, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (32, 6, 'b7bea7af48e935fd.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqARA19AAFuZ18W_ks439.jpg', 11, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (33, 7, '2b78b6fdabfd2fbe.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 7, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (34, 7, '5d5c57ab443f5fbb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAbNFZAADi9nT5SuM524.jpg', 8, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (35, 7, 'cef3c55b8caad783.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAMq2nAAGtjwY17fA932.jpg', 9, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (36, 7, '6eaaecc91b9ad059.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAXjRKAAFpxKwrSvY886.jpg', 10, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (37, 7, 'b7bea7af48e935fd.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqARA19AAFuZ18W_ks439.jpg', 11, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (38, 8, '7155bba4c363065f.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAVRWzAABUiOmA0ic932.jpg', 12, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (39, 8, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', 13, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (40, 8, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', 16, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (41, 8, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', 18, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (42, 9, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', 13, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (43, 9, '6ef342197c8095b6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAJllcAAEY0AkXL8M782.jpg', 14, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (44, 9, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', 16, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (45, 9, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', 18, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (46, 10, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', 13, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (47, 10, '34c390fe3ab2bab5.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAbqkuAAENKBtJukQ551.jpg', 15, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (48, 10, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', 16, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (49, 10, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', 18, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (50, 11, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', 13, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (51, 11, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', 16, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (52, 11, 'de33680f921e5838.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWASR1YAADsOUYB-2g312.jpg', 17, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (53, 11, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', 18, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (54, 12, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', 13, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (55, 12, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', 16, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (56, 12, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', 18, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (57, 12, '7155bba4c363065f.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAVRWzAABUiOmA0ic932.jpg', 12, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (58, 13, '19b35dc3ebe1579e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAQwdLAACW7swImE4752.jpg', 19, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (59, 13, '1009958cd1bc4e70.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAUJH9AACHXa0HPC8315.jpg', 20, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (60, 13, '76c7922bb9d226d0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAPVqkAAEoiSa9WoM463.jpg', 21, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (61, 13, '9316593be50948f8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaALrngAAHGDqdpFtU741.jpg', 22, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (62, 13, 'ee11befc0d6dfa18.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleASDLsAAFcVo0uNCs962.jpg', 24, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (63, 13, 'fa4ffba88e29ee03.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAJ6zNAADSblcDDEM109.jpg', 25, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (64, 13, 'a01d74c9878967fa.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAa3edAAKUHkq4s24909.jpg', 26, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (65, 14, '19b35dc3ebe1579e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAQwdLAACW7swImE4752.jpg', 19, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (66, 14, '1009958cd1bc4e70.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAUJH9AACHXa0HPC8315.jpg', 20, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (67, 14, '76c7922bb9d226d0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAPVqkAAEoiSa9WoM463.jpg', 21, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (68, 14, '89e57f95b32e5385.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAbYPbAAHJf20K7J4980.jpg', 23, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (69, 14, 'ee11befc0d6dfa18.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleASDLsAAFcVo0uNCs962.jpg', 24, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (70, 14, 'fa4ffba88e29ee03.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAJ6zNAADSblcDDEM109.jpg', 25, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (71, 14, 'a01d74c9878967fa.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAa3edAAKUHkq4s24909.jpg', 26, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (72, 15, '19b35dc3ebe1579e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAQwdLAACW7swImE4752.jpg', 19, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (73, 15, '1009958cd1bc4e70.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAUJH9AACHXa0HPC8315.jpg', 20, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (74, 15, '76c7922bb9d226d0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAPVqkAAEoiSa9WoM463.jpg', 21, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (75, 15, '89e57f95b32e5385.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAbYPbAAHJf20K7J4980.jpg', 23, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (76, 15, 'ee11befc0d6dfa18.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleASDLsAAFcVo0uNCs962.jpg', 24, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (77, 15, 'fa4ffba88e29ee03.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAJ6zNAADSblcDDEM109.jpg', 25, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (78, 15, 'a01d74c9878967fa.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAa3edAAKUHkq4s24909.jpg', 26, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (79, 16, '19b35dc3ebe1579e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAQwdLAACW7swImE4752.jpg', 19, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (80, 16, '1009958cd1bc4e70.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAUJH9AACHXa0HPC8315.jpg', 20, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (81, 16, '76c7922bb9d226d0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAPVqkAAEoiSa9WoM463.jpg', 21, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (82, 16, '9316593be50948f8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaALrngAAHGDqdpFtU741.jpg', 22, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (83, 16, 'ee11befc0d6dfa18.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleASDLsAAFcVo0uNCs962.jpg', 24, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (84, 16, 'fa4ffba88e29ee03.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAJ6zNAADSblcDDEM109.jpg', 25, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (85, 16, 'a01d74c9878967fa.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAa3edAAKUHkq4s24909.jpg', 26, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (86, 17, '23e94e5f9fa168d7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAaz7cAACS1bFmjlE652.jpg', 27, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (87, 17, '8ea8ac1e43eb09bc.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAe9mBAAHubDHNM_s207.jpg', 29, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (88, 17, '44b81ee60ba37250.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-ARs7SAAK2xlc5Kew601.jpg', 31, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (89, 17, 'ed289ba23e865e31.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AN7-lAAJRUqAcXEI304.jpg', 32, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (90, 17, 'bae6e6a540c68c48.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAWr6DAAOBwhDxkRg939.jpg', 33, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (91, 17, 'd37bc807dd3080d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAcfXYAAOtpRUCgAc311.jpg', 34, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (92, 17, 'b978ec45035aa218.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAFTPcAAM3hhhj828508.jpg', 35, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (93, 17, 'f496d9ae34b76554.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3GAUIstAAMFiaVe0I4100.jpg', 36, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (94, 18, '23e94e5f9fa168d7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAaz7cAACS1bFmjlE652.jpg', 27, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (95, 18, '5f679f4a7990fffb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAAHK_AAHxOsbi0Gc462.jpg', 28, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (96, 18, '44b81ee60ba37250.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-ARs7SAAK2xlc5Kew601.jpg', 31, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (97, 18, 'ed289ba23e865e31.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AN7-lAAJRUqAcXEI304.jpg', 32, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (98, 18, 'bae6e6a540c68c48.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAWr6DAAOBwhDxkRg939.jpg', 33, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (99, 18, 'd37bc807dd3080d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAcfXYAAOtpRUCgAc311.jpg', 34, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (100, 18, 'b978ec45035aa218.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAFTPcAAM3hhhj828508.jpg', 35, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (101, 18, 'f496d9ae34b76554.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3GAUIstAAMFiaVe0I4100.jpg', 36, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (102, 19, '23e94e5f9fa168d7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAaz7cAACS1bFmjlE652.jpg', 27, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (103, 19, 'ccaad675b92601b8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AU8esAAIV74ZBdhU344.jpg', 30, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (104, 19, '44b81ee60ba37250.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-ARs7SAAK2xlc5Kew601.jpg', 31, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (105, 19, 'ed289ba23e865e31.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AN7-lAAJRUqAcXEI304.jpg', 32, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (106, 19, 'bae6e6a540c68c48.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAWr6DAAOBwhDxkRg939.jpg', 33, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (107, 19, 'd37bc807dd3080d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAcfXYAAOtpRUCgAc311.jpg', 34, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (108, 19, 'b978ec45035aa218.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAFTPcAAM3hhhj828508.jpg', 35, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (109, 19, 'f496d9ae34b76554.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3GAUIstAAMFiaVe0I4100.jpg', 36, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (110, 20, '1d06cce763319de9.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AGdWUAADGjZ-bAuc105.jpg', 37, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (111, 20, '6057c618c32233d3.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AIR2DAAB34EDXR8I868.jpg', 38, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (112, 20, 'b701596e43b3433b.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH-AOqGjAABTm8Yp74U305.jpg', 40, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (113, 20, 'cc2d09a3feba8058.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAY1IuAADTekb4Aks567.jpg', 42, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (114, 20, 'a4eaf7bf9528fe4d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAWtMyAAGxs6Q350k510.jpg', 43, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (115, 20, '9306232083ca53d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAY4cIAAHCbpsLG8Y072.jpg', 44, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (116, 20, 'a55bc2b7a3b0ecfb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAC8WUAAGuYv8KYsE398.jpg', 47, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (117, 21, '6057c618c32233d3.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AIR2DAAB34EDXR8I868.jpg', 38, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (118, 21, '1eabe76b0f15b0d8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AQb87AAJYDnKvZoU838.jpg', 39, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (119, 21, '1fffb98409fa32c0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kICAaGuxAAKnO3DNDcY020.jpg', 41, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (120, 21, 'a55bc2b7a3b0ecfb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAC8WUAAGuYv8KYsE398.jpg', 47, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (121, 21, '21401b8a2c2b38f1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAa4cXAAJ021_HJN8583.jpg', 45, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (122, 21, '42ed4e6655d2acca.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAMJkPAAJdKCVoSR0812.jpg', 46, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (123, 22, '4a5fc6ff3ea986e9 (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0lieAGQHUAAIiC_lx9cU417.jpg', 48, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (124, 22, '6e82f1a4475f871e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqALXl6AAOvXBZbBYs429.jpg', 49, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (125, 22, 'cc2d09a3feba8058.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAARz7AAHeAIWIl4I126.jpg', 51, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (126, 22, '8d83227b75119532.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAE9CSAAIUaEugX8E823.jpg', 52, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (127, 22, '31bd117f36197b9d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAZ1P_AALOZHDbvCM585.jpg', 54, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (128, 22, 'e26709c6dbd6a1fe.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0li2AZLT5AAMhohWBIMs183.jpg', 57, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (129, 23, '4a5fc6ff3ea986e9 (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0lieAGQHUAAIiC_lx9cU417.jpg', 48, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (130, 23, '8d83227b75119532.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAE9CSAAIUaEugX8E823.jpg', 52, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (131, 23, '31bd117f36197b9d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAZ1P_AALOZHDbvCM585.jpg', 54, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (132, 23, 'ed854afc5bb54697.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liyAJ34AAAJLREFgZVM767.jpg', 55, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (133, 23, '0490b051476c112e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAJTluAAVP1d_tXYs725.jpg', 53, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (134, 24, '5b73a35dN94a5e30a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7aADmXLAAI5tpjy5CI066.jpg', 58, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (135, 24, '5b73a36dNd0a67203.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7yAfFv9AAM5ZEY69Uo453.jpg', 59, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (136, 24, '5b73a35aN92891309.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AKuQVAALmOIO3U5M856.jpg', 62, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (137, 24, '5b73a362Nc6b5f3fb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l76AKjn2AAlz4fZOF_8339.jpg', 63, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (138, 25, '5b73a36dNd0a67203.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7yAfFv9AAM5ZEY69Uo453.jpg', 59, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (139, 25, 'c035c2acf72e5ff1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7yALvPlAAO4xZsGHFI059.jpg', 60, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (140, 25, '5cdf66beN8aa239ab.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AaH20AATmWw3ydsA295.jpg', 61, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (141, 25, '5b73a362Nc6b5f3fb (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7-AVJfOAAlz4fZOF_8230.jpg', 64, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (142, 26, '7ffd8d2a4be62085.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAZ6jBAAGu7qTDWVA490.jpg', 65, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (143, 26, 'c406559b05bcf6d4.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAc7geAAFOkBl0eCU984.jpg', 66, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (144, 26, '7496d660c45c45ac.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAbtKiAAFs7t6eSJ0508.jpg', 67, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (145, 26, 'f8051c8538cb011e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 68, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (146, 26, 'b28620aae9d31c52.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yciAPvJGAARhSl8kZA8647.png', 69, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (147, 27, '7ffd8d2a4be62085.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAZ6jBAAGu7qTDWVA490.jpg', 65, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (148, 27, 'c406559b05bcf6d4.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAc7geAAFOkBl0eCU984.jpg', 66, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (149, 27, '7496d660c45c45ac.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAbtKiAAFs7t6eSJ0508.jpg', 67, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (150, 27, 'f8051c8538cb011e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 68, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (151, 27, 'b28620aae9d31c52.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yciAPvJGAARhSl8kZA8647.png', 69, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (152, 28, '7ffd8d2a4be62085.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAZ6jBAAGu7qTDWVA490.jpg', 65, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (153, 28, 'c406559b05bcf6d4.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAc7geAAFOkBl0eCU984.jpg', 66, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (154, 28, '7496d660c45c45ac.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAbtKiAAFs7t6eSJ0508.jpg', 67, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (155, 28, 'f8051c8538cb011e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 68, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (156, 28, 'b28620aae9d31c52.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yciAPvJGAARhSl8kZA8647.png', 69, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (157, 29, '72cec68481f86aff.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WATxItAAEcZnKxvfI617.jpg', 70, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (158, 29, '1b462be0fb148e88.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WAKv_cAAHKVw8AZqk151.jpg', 71, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (159, 29, 'dcc198936a4601de.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eAS25WAAEYg9IP_7o495.jpg', 73, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (160, 29, '1057db3d2a4deea2.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAAXYvAAGZqARg5F4191.jpg', 74, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (161, 29, 'bb4a19c813f7e61e.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAaMWAAANMuaHHijI678.png', 75, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (162, 29, '8c35313dfcbccf2a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iADJZ_AAIz437wUUc659.jpg', 76, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (163, 29, '0ed0cf1bb387059b.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1mAcqDEAAVe-gtfew4145.png', 77, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (164, 30, '1b462be0fb148e88.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WAKv_cAAHKVw8AZqk151.jpg', 71, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (165, 30, '507a23898221cd6a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eASPU7AAEiYRrkMww103.jpg', 72, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (166, 30, '1057db3d2a4deea2.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAAXYvAAGZqARg5F4191.jpg', 74, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (167, 30, 'bb4a19c813f7e61e.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAaMWAAANMuaHHijI678.png', 75, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (168, 30, '8c35313dfcbccf2a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iADJZ_AAIz437wUUc659.jpg', 76, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (169, 30, '0ed0cf1bb387059b.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1mAcqDEAAVe-gtfew4145.png', 77, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (170, 31, '72cec68481f86aff.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WATxItAAEcZnKxvfI617.jpg', 70, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (171, 31, '1b462be0fb148e88.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WAKv_cAAHKVw8AZqk151.jpg', 71, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (172, 31, '1057db3d2a4deea2.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAAXYvAAGZqARg5F4191.jpg', 74, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (173, 31, 'bb4a19c813f7e61e.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAaMWAAANMuaHHijI678.png', 75, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (174, 31, '8c35313dfcbccf2a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iADJZ_AAIz437wUUc659.jpg', 76, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (175, 31, '0ed0cf1bb387059b.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1mAcqDEAAVe-gtfew4145.png', 77, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (176, 32, '53f744c57c9bed0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAbhV8AACzhzshAss449.jpg', 79, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (177, 32, '09ee8484ac136e01.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAXnZ9AAExlZ_f0b0308.jpg', 80, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (178, 32, 'a4a448f9df8c715a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAEXECAAFZJnPg5O0915.jpg', 84, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (179, 32, 'a4a448f9df8c715a (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAe1vIAAFZJnPg5O0004.jpg', 86, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (180, 32, 'd6fb7d2f6cb06195.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MWAfxwgAAD8jFUkfao175.jpg', 87, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (181, 33, '5ad5104b7cff69f6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAcQ0_AAGxbDKQSm0286.jpg', 78, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (182, 33, '196d96f59897cf39.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSABFB2AAHD3bWoRhw015.jpg', 82, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (183, 33, '6cc3723538c7f7b3.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAPFONAAGchseVkoM398.jpg', 83, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (184, 33, 'a4a448f9df8c715a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAEXECAAFZJnPg5O0915.jpg', 84, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (185, 33, '374b25b22e90ed74.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAe9k4AAINKcLONPQ919.jpg', 85, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (186, 34, '4cd12718fd5e1bd7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AeAd45VAAF1GK3U-NE661.jpg', 88, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (187, 34, 'a61a13eb7e3489f1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AeASZitAAFcbBnUWrw650.jpg', 89, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (188, 34, 'c7b173293ee66929.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AuAIVVAAAFjH-WR0oA236.jpg', 92, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (189, 34, '4e9461a5b6e0136f.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AyARcFFAAKGCeWL410100.jpg', 94, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (190, 34, 'b33f63b0a7fab02d.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02A2Ae-VYAAbQwOM632g076.png', 95, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (191, 34, 'b526433a4d9e51ca.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02A2AXG14AAY1E5sugTg523.jpg', 96, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (192, 35, '0be857e07965a67e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AiAbf9pAAJ-rvQXZj4029.jpg', 90, '1', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (193, 35, 'b104fa8eec6f6de6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AuAfizUAAKkARuK5vg313.jpg', 91, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (194, 35, '64332079e66a291c.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AyAGFZzAAMnSgOWLUo218.jpg', 93, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (195, 35, 'b33f63b0a7fab02d.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02A2Ae-VYAAbQwOM632g076.png', 95, '0', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_image` VALUES (196, 35, 'a61a13eb7e3489f1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AeASZitAAFcbBnUWrw650.jpg', 89, '0', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for sku_info
-- ----------------------------
DROP TABLE IF EXISTS `sku_info`;
CREATE TABLE `sku_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'skuid',
  `spu_id` bigint NULL DEFAULT NULL COMMENT 'spuid',
  `price` decimal(10, 0) NULL DEFAULT NULL COMMENT '价格',
  `sku_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'sku名称',
  `sku_desc` varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '商品规格描述',
  `weight` decimal(10, 2) NULL DEFAULT NULL COMMENT '重量',
  `tm_id` bigint NULL DEFAULT NULL COMMENT '品牌id(冗余)',
  `category3_id` bigint NULL DEFAULT NULL COMMENT '三级品类id（冗余)',
  `sku_default_img` varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '默认显示图片地址(冗余)',
  `is_sale` tinyint NOT NULL DEFAULT 0 COMMENT '是否在售（1：是 0：否）',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'sku表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of sku_info
-- ----------------------------
INSERT INTO `sku_info` VALUES (1, 1, 6999, '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+512GB 冷杉绿 5G手机', '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+512GB 冷杉绿 5G手机', 1.00, 5, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAIpgZAAIvrX6L9fo612.jpg', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (2, 1, 6999, '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+512GB 冷杉绿 5G手机', '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+512GB 冷杉绿 5G手机', 1.00, 5, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAcbl2AAFopp2WGBQ404.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (3, 1, 6499, '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+256GB 经典黑 5G手机', '小米12S Ultra 骁龙8+旗舰处理器 徕卡光学镜头 2K超视感屏 120Hz高刷 67W快充 12GB+256GB 经典黑 5G手机', 1.00, 5, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAIpgZAAIvrX6L9fo612.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (4, 2, 999, 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 4GB+128GB 冰雾白 游戏智能手机 小米 红米', 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 4GB+128GB 冰雾白 游戏智能手机 小米 红米', 1.00, 1, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (5, 2, 999, 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 4GB+128GB 明月灰 游戏智能手机 小米 红米', 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 4GB+128GB 明月灰 游戏智能手机 小米 红米', 1.00, 1, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (6, 2, 1299, 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 8GB+128GB 冰雾白 游戏智能手机 小米 红米', 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 8GB+128GB 冰雾白 游戏智能手机 小米 红米', 1.00, 1, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (7, 2, 1299, 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 8GB+128GB 明月灰 游戏智能手机 小米 红米', 'Redmi 10X 4G Helio G85游戏芯 4800万超清四摄 5020mAh大电量 小孔全面屏 128GB大存储 8GB+128GB 明月灰 游戏智能手机 小米 红米', 1.00, 1, 61, 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (8, 3, 8197, 'Apple iPhone 12 (A2404) 64GB 黑色 支持移动联通电信5G 双卡双待手机', 'Apple iPhone 12 (A2404) 128GB 黑色 支持移动联通电信5G 双卡双待手机', 1.00, 2, 61, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAVRWzAABUiOmA0ic932.jpg', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (9, 3, 8197, 'Apple iPhone 12 (A2404) 64GB 红色 支持移动联通电信5G 双卡双待手机', 'Apple iPhone 12 (A2404) 128GB 红色 支持移动联通电信5G 双卡双待手机', 1.00, 2, 61, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAJllcAAEY0AkXL8M782.jpg', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (10, 3, 8197, 'Apple iPhone 12 (A2404) 64GB 蓝色 支持移动联通电信5G 双卡双待手机', 'Apple iPhone 12 (A2404) 128GB 红色 支持移动联通电信5G 双卡双待手机', 1.00, 2, 61, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAbqkuAAENKBtJukQ551.jpg', 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (11, 3, 8197, 'Apple iPhone 12 (A2404) 64GB 白色 支持移动联通电信5G 双卡双待手机', 'Apple iPhone 12 (A2404) 128GB 红色 支持移动联通电信5G 双卡双待手机', 1.00, 2, 61, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWASR1YAADsOUYB-2g312.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (12, 3, 9197, 'Apple iPhone 12 (A2404) 128GB 黑色 支持移动联通电信5G 双卡双待手机', 'Apple iPhone 12 (A2404) 128GB 黑色 支持移动联通电信5G 双卡双待手机', 1.00, 2, 61, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAVRWzAABUiOmA0ic932.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (13, 4, 9899, '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i9-12900H RTX3060 钛晶灰', '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i9-12900H RTX3060 钛晶灰', 4.00, 3, 287, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaALrngAAHGDqdpFtU741.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (14, 4, 11999, '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i9-12900H RTX3070Ti 钛晶灰', '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i9-12900H RTX3070Ti 钛晶灰', 1.00, 3, 287, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAbYPbAAHJf20K7J4980.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (15, 4, 9799, '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i7-12700H 512G RTX3060 钛晶灰', '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i7-12700H 512G RTX3060 钛晶灰', 1.00, 3, 287, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAbYPbAAHJf20K7J4980.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (16, 4, 10599, '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i7-12700H RTX3060 冰魄白', '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑 i7-12700H RTX3060 冰魄白', 1.00, 3, 287, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaALrngAAHGDqdpFtU741.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (17, 5, 6699, 'TCL 65Q10 65英寸 QLED原色量子点电视 安桥音响 AI声控智慧屏 超薄全面屏 MEMC防抖 3+32GB 平板电视', 'TCL 65Q10 65英寸 QLED原色量子点电视 安桥音响 AI声控智慧屏 超薄全面屏 MEMC防抖 3+32GB 平板电视', 23.00, 4, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAe9mBAAHubDHNM_s207.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (18, 5, 9199, 'TCL 75Q10 75英寸 QLED原色量子点电视 安桥音响 AI声控智慧屏 超薄全面屏 MEMC防抖 3+32GB 平板电视', 'TCL 75Q10 75英寸 QLED原色量子点电视 安桥音响 AI声控智慧屏 超薄全面屏 MEMC防抖 3+32GB 平板电视', 25.00, 4, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAAHK_AAHxOsbi0Gc462.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (19, 5, 11999, 'TCL 85Q6 85英寸 巨幕私人影院电视 4K超高清 AI智慧屏 全景全面屏 MEMC运动防抖 2+16GB 液晶平板电视机', 'TCL 85Q6 85英寸 巨幕私人影院电视 4K超高清 AI智慧屏 全景全面屏 MEMC运动防抖 2+16GB 液晶平板电视机', 30.00, 4, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AU8esAAIV74ZBdhU344.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (20, 6, 2899, '小米电视E65X 65英寸 全面屏 4K超高清HDR 蓝牙遥控内置小爱 2+8GB AI人工智能液晶网络平板电视 L65M5-EA', '小米电视E65X 65英寸 全面屏 4K超高清HDR 蓝牙遥控内置小爱 2+8GB AI人工智能液晶网络平板电视 L65M5-EA', 23.00, 5, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAWtMyAAGxs6Q350k510.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (21, 6, 3299, '小米电视4A 70英寸 4K超高清 HDR 二级能效 2GB+16GB L70M5-4A 内置小爱 智能网络液晶平板教育电视', '小米电视4A 70英寸 4K超高清 HDR 二级能效 2GB+16GB L70M5-4A 内置小爱 智能网络液晶平板教育电视', 28.00, 5, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kICAaGuxAAKnO3DNDcY020.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (22, 7, 39, '十月稻田 长粒香大米 东北大米 东北香米 5kg', '十月稻田 长粒香大米 东北大米 东北香米 5kg', 5.00, 6, 803, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAARz7AAHeAIWIl4I126.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (23, 7, 40, '十月稻田 辽河长粒香 东北大米 5kg', '十月稻田 辽河长粒香 东北大米 5kg', 5.00, 6, 803, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAJTluAAVP1d_tXYs725.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (24, 8, 11, '金沙河面条 原味银丝挂面 龙须面 方便速食拉面 清汤面 900g', '金沙河面条 原味银丝挂面 龙须面 方便速食拉面 清汤面 900g', 0.90, 7, 803, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AKuQVAALmOIO3U5M856.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (25, 8, 23, '金沙河面条 银丝挂面900g*3包 爽滑 细面条 龙须面 速食面', '金沙河面条 银丝挂面900g*3包 爽滑 细面条 龙须面 速食面', 2.70, 7, 803, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AaH20AATmWw3ydsA295.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (26, 9, 129, '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Y01复古红 百搭气质 璀璨金钻哑光唇膏 ', '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Y01复古红 百搭气质 璀璨金钻哑光唇膏 ', 1.00, 8, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (27, 9, 129, '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Z02少女红 活力青春 璀璨金钻哑光唇膏 ', '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Z02少女红 活力青春 璀璨金钻哑光唇膏 ', 1.00, 8, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (28, 9, 129, '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Z03女王红 性感冷艳 璀璨金钻哑光唇膏 ', '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 Z03女王红 性感冷艳 璀璨金钻哑光唇膏 ', 1.00, 8, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (29, 10, 69, 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M01醉蔷薇', 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M01醉蔷薇', 1.00, 9, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eAS25WAAEYg9IP_7o495.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (30, 10, 69, 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M02干玫瑰', 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M02干玫瑰', 1.00, 9, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eASPU7AAEiYRrkMww103.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (31, 10, 69, 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M03赤茶', 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏 M03赤茶', 1.00, 9, 477, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WATxItAAEcZnKxvfI617.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (32, 11, 300, '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT 5号淡香水35ml', '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT 5号淡香水35ml', 1.00, 11, 473, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAbhV8AACzhzshAss449.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (33, 11, 488, '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT 粉邂逅淡香水35ml', '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT 粉邂逅淡香水35ml', 1.00, 11, 473, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSABFB2AAHD3bWoRhw015.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (34, 12, 3927, '华为智慧屏V55i-J 55英寸 HEGE-550B 4K全面屏智能电视机 多方视频通话 AI升降摄像头 银钻灰 京品家电', '华为智慧屏V55i-J 55英寸 HEGE-550B 4K全面屏智能电视机 多方视频通话 AI升降摄像头 银钻灰 京品家电', 30.00, 3, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AyARcFFAAKGCeWL410100.jpg', 0, '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_info` VALUES (35, 12, 5499, '华为智慧屏V65i 65英寸 HEGE-560B 4K全面屏智能电视机 多方视频通话 AI升降摄像头 4GB+32GB 星际黑', '华为智慧屏V65i 65英寸 HEGE-560B 4K全面屏智能电视机 多方视频通话 AI升降摄像头 4GB+32GB 星际黑', 35.00, 3, 86, 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AiAbf9pAAJ-rvQXZj4029.jpg', 0, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for sku_sale_attr_value
-- ----------------------------
DROP TABLE IF EXISTS `sku_sale_attr_value`;
CREATE TABLE `sku_sale_attr_value`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `spu_id` int NULL DEFAULT NULL COMMENT 'spu_id(冗余)',
  `sale_attr_value_id` bigint NULL DEFAULT NULL COMMENT '销售属性值id',
  `sale_attr_id` bigint NULL DEFAULT NULL COMMENT '销售属性id',
  `sale_attr_name` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '销售属性名称',
  `sale_attr_value_name` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '销售属性值名称',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'sku销售属性值表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of sku_sale_attr_value
-- ----------------------------
INSERT INTO `sku_sale_attr_value` VALUES (1, 1, 1, 1, 1, '颜色', '冷杉绿', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (2, 1, 1, 4, 2, '版本', '12G+512G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (3, 2, 1, 2, 1, '颜色', '经典黑', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (4, 2, 1, 4, 2, '版本', '12G+512G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (5, 3, 1, 2, 1, '颜色', '经典黑', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (6, 3, 1, 3, 2, '版本', '12G+256G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (7, 4, 2, 5, 1, '颜色', '冰雾白', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (8, 4, 2, 7, 2, '版本', '4G+128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (9, 5, 2, 6, 1, '颜色', '明月灰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (10, 5, 2, 7, 2, '版本', '4G+128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (11, 6, 2, 5, 1, '颜色', '冰雾白', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (12, 6, 2, 8, 2, '版本', '8G+128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (13, 7, 2, 6, 1, '颜色', '明月灰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (14, 7, 2, 8, 2, '版本', '8G+128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (15, 8, 3, 9, 1, '颜色', '黑色', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (16, 8, 3, 13, 2, '版本', '64G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (17, 9, 3, 10, 1, '颜色', '红色', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (18, 9, 3, 13, 2, '版本', '64G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (19, 10, 3, 11, 1, '颜色', '蓝色', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (20, 10, 3, 13, 2, '版本', '64G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (21, 11, 3, 12, 1, '颜色', '白色', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (22, 11, 3, 13, 2, '版本', '64G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (23, 12, 3, 9, 1, '颜色', '黑色', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (24, 12, 3, 14, 2, '版本', '128G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (25, 13, 4, 15, 5, '处理器或内存', 'i9-12700H 512G RTX3060 灰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (27, 14, 4, 16, 5, '处理器或内存', 'i9-12700H 512G RTX3070Ti 灰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (29, 15, 4, 17, 5, '处理器或内存', 'i7-12700H 512G RTX3060 灰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (31, 16, 4, 18, 5, '处理器或内存', 'i7-12700H 512G+2T RTX3060 白', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (33, 17, 5, 19, 3, '尺码', '65英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (34, 18, 5, 20, 3, '尺码', '75英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (35, 19, 5, 21, 3, '尺码', '85英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (36, 20, 6, 22, 3, '尺码', '65英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (37, 21, 6, 23, 3, '尺码', '70英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (38, 22, 7, 24, 4, '类别', '长粒香大米5KG', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (39, 23, 7, 25, 4, '类别', '辽河长粒香5KG', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (40, 24, 8, 26, 4, '类别', '900G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (41, 25, 8, 27, 4, '类别', '3*900G', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (42, 26, 9, 28, 4, '类别', 'Y01复古红 百搭气质', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (43, 27, 9, 29, 4, '类别', 'Z02少女红 活力青春', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (44, 28, 9, 30, 4, '类别', 'Z03女王红 性感冷艳', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (45, 29, 10, 31, 4, '类别', 'M01醉蔷薇', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (46, 30, 10, 32, 4, '类别', 'M02干玫瑰', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (47, 31, 10, 33, 4, '类别', 'M03赤茶', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (48, 32, 11, 34, 4, '类别', '5号淡香水35ml', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (49, 33, 11, 35, 4, '类别', '5号粉邂逅淡香水35ml', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (50, 34, 12, 36, 3, '尺码', '55英寸', '2021-12-14 00:00:00', NULL);
INSERT INTO `sku_sale_attr_value` VALUES (51, 35, 12, 37, 3, '尺码', '65英寸', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for spu_image
-- ----------------------------
DROP TABLE IF EXISTS `spu_image`;
CREATE TABLE `spu_image`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `spu_id` bigint NULL DEFAULT NULL COMMENT '商品id',
  `img_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片名称',
  `img_url` varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '图片路径',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'spu图片表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of spu_image
-- ----------------------------
INSERT INTO `spu_image` VALUES (1, 1, '2ff0882c9607e57c.jpg2', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAIpgZAAIvrX6L9fo612.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (2, 1, 'ead186426badb626.jpg3', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAHpFuAACTenfIJWo734.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (3, 1, 'b58ab2d79b859f39.jpg3', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAcbl2AAFopp2WGBQ404.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (4, 1, '0d93a071c839d890.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmATs5EAABriLbF9vE207.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (5, 1, 'a7b1125239354d0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAeWopAAEtpBjP1VQ788.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (6, 1, '6029cb2c2b2c7668.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rfvmAZJX2AAGmVo8Tz9s683.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (7, 2, '2b78b6fdabfd2fbe.jpg', 'http://47.93.148.192:8080/group1/M00/00/01/rBHu8l-rgJqAHPnoAAF9hoDNfsc505.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (8, 2, '5d5c57ab443f5fbb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAbNFZAADi9nT5SuM524.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (9, 2, 'cef3c55b8caad783.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAMq2nAAGtjwY17fA932.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (10, 2, '6eaaecc91b9ad059.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqAXjRKAAFpxKwrSvY886.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (11, 2, 'b7bea7af48e935fd.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgJqARA19AAFuZ18W_ks439.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (12, 3, '7155bba4c363065f.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAVRWzAABUiOmA0ic932.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (13, 3, '2689bc534d570eaf.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAO2oYAAEw9kY2VKk982.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (14, 3, '6ef342197c8095b6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAJllcAAEY0AkXL8M782.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (15, 3, '34c390fe3ab2bab5.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAbqkuAAENKBtJukQ551.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (16, 3, '7ae59d1d962f0965.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWAFeQLAAEt9MLZnho584.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (17, 3, 'de33680f921e5838.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWASR1YAADsOUYB-2g312.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (18, 3, 'f73bfe30f5ec641a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-rgfWABhwlAAEjBwwVkrI735.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (19, 4, '19b35dc3ebe1579e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAQwdLAACW7swImE4752.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (20, 4, '1009958cd1bc4e70.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklWAUJH9AACHXa0HPC8315.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (21, 4, '76c7922bb9d226d0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAPVqkAAEoiSa9WoM463.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (22, 4, '9316593be50948f8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaALrngAAHGDqdpFtU741.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (23, 4, '89e57f95b32e5385.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-sklaAbYPbAAHJf20K7J4980.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (24, 4, 'ee11befc0d6dfa18.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleASDLsAAFcVo0uNCs962.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (25, 4, 'fa4ffba88e29ee03.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAJ6zNAADSblcDDEM109.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (26, 4, 'a01d74c9878967fa.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-skleAa3edAAKUHkq4s24909.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (27, 5, '23e94e5f9fa168d7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAaz7cAACS1bFmjlE652.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (28, 5, '5f679f4a7990fffb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAAHK_AAHxOsbi0Gc462.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (29, 5, '8ea8ac1e43eb09bc.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2uAe9mBAAHubDHNM_s207.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (30, 5, 'ccaad675b92601b8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AU8esAAIV74ZBdhU344.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (31, 5, '44b81ee60ba37250.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-ARs7SAAK2xlc5Kew601.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (32, 5, 'ed289ba23e865e31.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i2-AN7-lAAJRUqAcXEI304.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (33, 5, 'bae6e6a540c68c48.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAWr6DAAOBwhDxkRg939.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (34, 5, 'd37bc807dd3080d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAcfXYAAOtpRUCgAc311.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (35, 5, 'b978ec45035aa218.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3CAFTPcAAM3hhhj828508.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (36, 5, 'f496d9ae34b76554.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0i3GAUIstAAMFiaVe0I4100.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (37, 6, '1d06cce763319de9.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AGdWUAADGjZ-bAuc105.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (38, 6, '6057c618c32233d3.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AIR2DAAB34EDXR8I868.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (39, 6, '1eabe76b0f15b0d8.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH6AQb87AAJYDnKvZoU838.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (40, 6, 'b701596e43b3433b.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kH-AOqGjAABTm8Yp74U305.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (41, 6, '1fffb98409fa32c0.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kICAaGuxAAKnO3DNDcY020.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (42, 6, 'cc2d09a3feba8058.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAY1IuAADTekb4Aks567.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (43, 6, 'a4eaf7bf9528fe4d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAWtMyAAGxs6Q350k510.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (44, 6, '9306232083ca53d6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIGAY4cIAAHCbpsLG8Y072.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (45, 6, '21401b8a2c2b38f1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAa4cXAAJ021_HJN8583.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (46, 6, '42ed4e6655d2acca.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAMJkPAAJdKCVoSR0812.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (47, 6, 'a55bc2b7a3b0ecfb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0kIKAC8WUAAGuYv8KYsE398.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (48, 7, '4a5fc6ff3ea986e9 (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0lieAGQHUAAIiC_lx9cU417.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (49, 7, '6e82f1a4475f871e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqALXl6AAOvXBZbBYs429.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (50, 7, '4a5fc6ff3ea986e9.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAR6hmAAIiC_lx9cU271.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (51, 7, 'cc2d09a3feba8058.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAARz7AAHeAIWIl4I126.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (52, 7, '8d83227b75119532.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liqAE9CSAAIUaEugX8E823.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (53, 7, '0490b051476c112e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAJTluAAVP1d_tXYs725.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (54, 7, '31bd117f36197b9d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liuAZ1P_AALOZHDbvCM585.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (55, 7, 'ed854afc5bb54697.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0liyAJ34AAAJLREFgZVM767.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (56, 7, 'dc8784f5f37700e7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0li2ATv_qAAJbSlyzKD8549.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (57, 7, 'e26709c6dbd6a1fe.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0li2AZLT5AAMhohWBIMs183.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (58, 8, '5b73a35dN94a5e30a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7aADmXLAAI5tpjy5CI066.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (59, 8, '5b73a36dNd0a67203.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7yAfFv9AAM5ZEY69Uo453.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (60, 8, 'c035c2acf72e5ff1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7yALvPlAAO4xZsGHFI059.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (61, 8, '5cdf66beN8aa239ab.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AaH20AATmWw3ydsA295.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (62, 8, '5b73a35aN92891309.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l72AKuQVAALmOIO3U5M856.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (63, 8, '5b73a362Nc6b5f3fb.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l76AKjn2AAlz4fZOF_8339.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (64, 8, '5b73a362Nc6b5f3fb (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0l7-AVJfOAAlz4fZOF_8230.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (65, 9, '7ffd8d2a4be62085.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAZ6jBAAGu7qTDWVA490.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (66, 9, 'c406559b05bcf6d4.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAc7geAAFOkBl0eCU984.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (67, 9, '7496d660c45c45ac.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAbtKiAAFs7t6eSJ0508.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (68, 9, 'f8051c8538cb011e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yceAC5dfAAH_Rtwwzq8880.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (69, 9, 'b28620aae9d31c52.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0yciAPvJGAARhSl8kZA8647.png', NULL, NULL);
INSERT INTO `spu_image` VALUES (70, 10, '72cec68481f86aff.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WATxItAAEcZnKxvfI617.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (71, 10, '1b462be0fb148e88.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1WAKv_cAAHKVw8AZqk151.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (72, 10, '507a23898221cd6a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eASPU7AAEiYRrkMww103.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (73, 10, 'dcc198936a4601de.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1eAS25WAAEYg9IP_7o495.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (74, 10, '1057db3d2a4deea2.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAAXYvAAGZqARg5F4191.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (75, 10, 'bb4a19c813f7e61e.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iAaMWAAANMuaHHijI678.png', NULL, NULL);
INSERT INTO `spu_image` VALUES (76, 10, '8c35313dfcbccf2a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1iADJZ_AAIz437wUUc659.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (77, 10, '0ed0cf1bb387059b.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-0y1mAcqDEAAVe-gtfew4145.png', NULL, NULL);
INSERT INTO `spu_image` VALUES (78, 11, '5ad5104b7cff69f6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAcQ0_AAGxbDKQSm0286.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (79, 11, '53f744c57c9bed0d.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAbhV8AACzhzshAss449.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (80, 11, '09ee8484ac136e01.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MKAXnZ9AAExlZ_f0b0308.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (81, 11, '6b1ca0a6e796e5c9.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAIPzOAAHFKmckxVk619.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (82, 11, '196d96f59897cf39.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSABFB2AAHD3bWoRhw015.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (83, 11, '6cc3723538c7f7b3.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAPFONAAGchseVkoM398.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (84, 11, 'a4a448f9df8c715a.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAEXECAAFZJnPg5O0915.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (85, 11, '374b25b22e90ed74.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAe9k4AAINKcLONPQ919.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (86, 11, 'a4a448f9df8c715a (1).jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MSAe1vIAAFZJnPg5O0004.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (87, 11, 'd6fb7d2f6cb06195.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-00MWAfxwgAAD8jFUkfao175.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (88, 12, '4cd12718fd5e1bd7.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AeAd45VAAF1GK3U-NE661.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (89, 12, 'a61a13eb7e3489f1.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AeASZitAAFcbBnUWrw650.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (90, 12, '0be857e07965a67e.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AiAbf9pAAJ-rvQXZj4029.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (91, 12, 'b104fa8eec6f6de6.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AuAfizUAAKkARuK5vg313.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (92, 12, 'c7b173293ee66929.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AuAIVVAAAFjH-WR0oA236.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (93, 12, '64332079e66a291c.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AyAGFZzAAMnSgOWLUo218.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (94, 12, '4e9461a5b6e0136f.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02AyARcFFAAKGCeWL410100.jpg', NULL, NULL);
INSERT INTO `spu_image` VALUES (95, 12, 'b33f63b0a7fab02d.png', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02A2Ae-VYAAbQwOM632g076.png', NULL, NULL);
INSERT INTO `spu_image` VALUES (96, 12, 'b526433a4d9e51ca.jpg', 'http://47.93.148.192:8080/group1/M00/00/02/rBHu8l-02A2AXG14AAY1E5sugTg523.jpg', NULL, NULL);

-- ----------------------------
-- Table structure for spu_info
-- ----------------------------
DROP TABLE IF EXISTS `spu_info`;
CREATE TABLE `spu_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'spu_id',
  `spu_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'spu名称',
  `description` varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '描述信息',
  `category3_id` bigint NULL DEFAULT NULL COMMENT '三级品类id',
  `tm_id` bigint NULL DEFAULT NULL COMMENT '品牌id',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'spu表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of spu_info
-- ----------------------------
INSERT INTO `spu_info` VALUES (1, '小米12sultra', '小米10', 61, 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (2, 'Redmi 10X', 'Redmi 10X', 61, 1, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (3, 'Apple iPhone 12', 'Apple iPhone 12', 61, 2, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (4, '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑', '联想（Lenovo） 拯救者Y9000P 2022 16英寸游戏笔记本电脑', 287, 3, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (5, 'TCL巨幕私人影院电视 4K超高清 AI智慧屏  液晶平板电视机', 'TCL巨幕私人影院电视 4K超高清 AI智慧屏  液晶平板电视机', 86, 4, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (6, '小米电视 内置小爱 智能网络液晶平板教育电视', '小米电视 内置小爱 智能网络液晶平板教育电视', 86, 5, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (7, '十月稻田 长粒香大米 东北大米 东北香米 5kg', '十月稻田 长粒香大米 东北大米 东北香米 5kg', 803, 6, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (8, '金沙河面条 原味银丝挂面 龙须面 方便速食拉面 清汤面 900g', '金沙河面条 原味银丝挂面 龙须面 方便速食拉面 清汤面 900g', 803, 7, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (9, '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 ', '索芙特i-Softto 口红不掉色唇膏保湿滋润 璀璨金钻哑光唇膏 ', 477, 8, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (10, 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏', 'CAREMiLLE珂曼奶油小方口红 雾面滋润保湿持久丝缎唇膏', 477, 9, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (11, '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT ', '香奈儿（Chanel）女士香水5号香水 粉邂逅柔情淡香水EDT ', 473, 11, '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_info` VALUES (12, '华为智慧屏 4K全面屏智能电视机', '华为智慧屏 4K全面屏智能电视机', 86, 3, '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for spu_poster
-- ----------------------------
DROP TABLE IF EXISTS `spu_poster`;
CREATE TABLE `spu_poster`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `spu_id` bigint NULL DEFAULT NULL COMMENT '商品id',
  `img_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '文件名称',
  `img_url` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '文件路径',
  `create_time` datetime NOT NULL COMMENT '创建时间',
  `operate_time` datetime NOT NULL COMMENT '更新时间',
  `is_deleted` tinyint NOT NULL DEFAULT 0 COMMENT '逻辑删除 1（true）已删除， 0（false）未删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '商品海报表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of spu_poster
-- ----------------------------

-- ----------------------------
-- Table structure for spu_sale_attr
-- ----------------------------
DROP TABLE IF EXISTS `spu_sale_attr`;
CREATE TABLE `spu_sale_attr`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号(业务中无关联)',
  `spu_id` bigint NULL DEFAULT NULL COMMENT '商品id',
  `base_sale_attr_id` bigint NULL DEFAULT NULL COMMENT '销售属性id',
  `sale_attr_name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '销售属性名称(冗余)',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'spu销售属性表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of spu_sale_attr
-- ----------------------------
INSERT INTO `spu_sale_attr` VALUES (1, 1, 1, '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (2, 1, 2, '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (3, 2, 1, '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (4, 2, 2, '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (5, 3, 1, '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (6, 3, 2, '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (7, 4, 5, '处理器或显卡', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (9, 5, 3, '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (10, 6, 3, '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (11, 7, 4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (12, 8, 4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (13, 9, 4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (14, 10, 4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (15, 11, 4, '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr` VALUES (16, 12, 3, '尺码', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for spu_sale_attr_value
-- ----------------------------
DROP TABLE IF EXISTS `spu_sale_attr_value`;
CREATE TABLE `spu_sale_attr_value`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '销售属性值编号',
  `spu_id` bigint NULL DEFAULT NULL COMMENT 'spuid',
  `base_sale_attr_id` bigint NULL DEFAULT NULL COMMENT '销售属性id',
  `sale_attr_value_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '销售属性值名称',
  `sale_attr_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '销售属性名称(冗余)',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'spu销售属性值表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of spu_sale_attr_value
-- ----------------------------
INSERT INTO `spu_sale_attr_value` VALUES (1, 1, 1, '冷杉绿', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (2, 1, 1, '经典黑', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (3, 1, 2, '12G+256G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (4, 1, 2, '12G+512G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (5, 2, 1, '冰雾白', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (6, 2, 1, '明月灰', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (7, 2, 2, '4G+128G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (8, 2, 2, '8G+128G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (9, 3, 1, '黑色', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (10, 3, 1, '红色', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (11, 3, 1, '蓝色', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (12, 3, 1, '白色', '颜色', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (13, 3, 2, '64G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (14, 3, 2, '128G', '版本', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (15, 4, 5, 'i9-12700H 512G RTX3060 灰', '处理器或内存', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (16, 4, 5, 'i9-12700H 512G RTX3070Ti 灰', '处理器或内存', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (17, 4, 5, 'i7-12700H 512G RTX3060 灰', '处理器或内存', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (18, 4, 5, 'i7-12700H 512G+2T RTX3060 白', '处理器或内存', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (19, 5, 3, '65英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (20, 5, 3, '75英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (21, 5, 3, '85英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (22, 6, 3, '65英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (23, 6, 3, '70英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (24, 7, 4, '长粒香大米5KG', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (25, 7, 4, '辽河长粒香5KG', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (26, 8, 4, '900G', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (27, 8, 4, '3*900G', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (28, 9, 4, 'Y01复古红 百搭气质', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (29, 9, 4, 'Z02少女红 活力青春', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (30, 9, 4, 'Z03女王红 性感冷艳', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (31, 10, 4, 'M01醉蔷薇', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (32, 10, 4, 'M02干玫瑰', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (33, 10, 4, 'M03赤茶', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (34, 11, 4, '5号淡香水35ml', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (35, 11, 4, '5号粉邂逅淡香水35ml', '类别', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (36, 12, 3, '55英寸', '尺码', '2021-12-14 00:00:00', NULL);
INSERT INTO `spu_sale_attr_value` VALUES (37, 12, 3, '65英寸', '尺码', '2021-12-14 00:00:00', NULL);

-- ----------------------------
-- Table structure for user_address
-- ----------------------------
DROP TABLE IF EXISTS `user_address`;
CREATE TABLE `user_address`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `user_id` bigint NULL DEFAULT NULL COMMENT '用户id',
  `province_id` bigint NULL DEFAULT NULL COMMENT '省份id',
  `user_address` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户地址',
  `consignee` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '收件人',
  `phone_num` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '联系方式',
  `is_default` varchar(1) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '是否是默认',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '用户地址表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of user_address
-- ----------------------------

-- ----------------------------
-- Table structure for user_info
-- ----------------------------
DROP TABLE IF EXISTS `user_info`;
CREATE TABLE `user_info`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `login_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户名称',
  `nick_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户昵称',
  `passwd` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户密码',
  `name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户姓名',
  `phone_num` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '手机号',
  `email` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '邮箱',
  `head_img` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '头像',
  `user_level` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户级别',
  `birthday` date NULL DEFAULT NULL COMMENT '用户生日',
  `gender` varchar(1) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '性别 M男,F女',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `operate_time` datetime NULL DEFAULT NULL COMMENT '修改时间',
  `status` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '状态',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '用户表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of user_info
-- ----------------------------
INSERT INTO `user_info` VALUES (1, '5vifw38y83', '亮政', NULL, '马亮政', '13624443478', '5vifw38y83@163.com', NULL, '3', '1998-05-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (2, 'maoddwb', '珍贞', NULL, '韩珍贞', '13796741171', 'maoddwb@0355.net', NULL, '3', '1987-12-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (3, 'stgzyf0g', '功松', NULL, '范功松', '13256727988', 'stgzyf0g@263.net', NULL, '2', '1974-04-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (4, '7bgplo7da6h2', '勤勤', NULL, '南门勤', '13418647143', '7bgplo7da6h2@163.com', NULL, '1', '2001-03-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (5, 'k3mmqahyod', '功松', NULL, '苏功松', '13539579772', 'k3mmqahyod@126.com', NULL, '2', '1989-02-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (6, 't7dk2h', '冰冰', NULL, '淳于冰', '13178654378', 't7dk2h@263.net', NULL, '1', '1997-12-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (7, 'vihcj30p1', '豪心', NULL, '魏豪心', '13956932645', 'vihcj30p1@live.com', NULL, '1', '1991-06-07', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (8, '02r2ahx', '卿卿', NULL, '穆卿', '13412413361', '02r2ahx@sohu.com', NULL, '1', '2001-07-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (9, 'mjhrxnu', '武新', NULL, '罗武新', '13617856358', 'mjhrxnu@yahoo.com', NULL, '1', '2001-08-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (10, 'kwua2155', '纨纨', NULL, '姜纨', '13742843828', 'kwua2155@163.net', NULL, '3', '1997-11-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (11, 'cmgwb7ppkin', '阿雄', NULL, '姚雄', '13429591392', 'cmgwb7ppkin@qq.com', NULL, '3', '1996-06-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (12, '60y6nnx', '咏卿', NULL, '严咏卿', '13876615939', '60y6nnx@yahoo.com.cn', NULL, '1', '1994-04-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (13, '3m03hlymsg3t', '茂进', NULL, '欧阳茂进', '13298737833', '3m03hlymsg3t@3721.net', NULL, '1', '1996-05-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (14, 'dn8h5z965', '玉玉', NULL, '钱玉', '13547441827', 'dn8h5z965@163.com', NULL, '1', '2001-02-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (15, '1rm3htq', '阿炎', NULL, '马炎', '13292258292', '1rm3htq@qq.com', NULL, '1', '1999-03-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (16, 'h6ejz7', '洁梅', NULL, '顾洁梅', '13148353964', 'h6ejz7@msn.com', NULL, '1', '1967-11-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (17, 'ggycwk2b5', '阿善', NULL, '钱善', '13425528535', 'ggycwk2b5@163.com', NULL, '1', '1968-08-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (18, 'x2egtg6p8', '婉娴', NULL, '伍婉娴', '13597716793', 'x2egtg6p8@3721.net', NULL, '2', '1982-03-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (19, 'dk7nd4iuggoo', '阿亮', NULL, '姚亮', '13232515756', 'dk7nd4iuggoo@yahoo.com.cn', NULL, '1', '1995-12-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (20, 'p5ia3ks', '茜秋', NULL, '秦茜秋', '13823771879', 'p5ia3ks@yahoo.com.cn', NULL, '2', '1985-05-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (21, 'hjhqtwl2', '振壮', NULL, '黄振壮', '13889387431', 'hjhqtwl2@126.com', NULL, '1', '2002-11-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (22, 'g6jqg3vzq9', '亚宜', NULL, '熊亚宜', '13893783542', 'g6jqg3vzq9@googlemail.com', NULL, '2', '1980-07-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (23, 'ahk0cbkkajv', '德行', NULL, '舒德行', '13584948943', 'ahk0cbkkajv@aol.com', NULL, '1', '1969-10-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (24, 'tx2jncfen0js', '悦悦', NULL, '柳悦', '13615362171', 'tx2jncfen0js@263.net', NULL, '1', '1978-09-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (25, '65swoxxt', '义兴', NULL, '唐义兴', '13976838475', '65swoxxt@263.net', NULL, '1', '1980-01-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (26, 'i023bfy23y', '素云', NULL, '穆素云', '13218631878', 'i023bfy23y@googlemail.com', NULL, '1', '1969-09-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (27, '2r5r9nx5cm', '盛雄', NULL, '于盛雄', '13763784866', '2r5r9nx5cm@aol.com', NULL, '1', '1974-08-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (28, 'uz6ce9', '倩倩', NULL, '尉迟倩', '13146425993', 'uz6ce9@msn.com', NULL, '3', '1968-05-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (29, 'l7il2i4mr', '力明', NULL, '郝力明', '13525978189', 'l7il2i4mr@aol.com', NULL, '2', '1979-09-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (30, 'so0vah1oz', '宁宁', NULL, '冯宁', '13352624982', 'so0vah1oz@3721.net', NULL, '1', '1995-03-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (31, 'brgh2ub', '泽晨', NULL, '袁泽晨', '13511229488', 'brgh2ub@3721.net', NULL, '1', '1972-12-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (32, 'orpu36szbp', '颖颖', NULL, '范颖', '13634137917', 'orpu36szbp@126.com', NULL, '1', '1970-10-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (33, 'd2uw8al', '贵福', NULL, '茅贵福', '13577486681', 'd2uw8al@263.net', NULL, '1', '1972-06-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (34, 'bra8fz', '柔竹', NULL, '司空柔竹', '13317542416', 'bra8fz@yahoo.com', NULL, '1', '2006-05-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (35, 'amantuyy0', '阿行', NULL, '鲍行', '13527165486', 'amantuyy0@ask.com', NULL, '1', '1991-04-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (36, 'amkowmh', '婵雁', NULL, '葛婵雁', '13971977259', 'amkowmh@126.com', NULL, '1', '1984-06-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (37, '8q69ui', '鸣朋', NULL, '司徒鸣朋', '13141139783', '8q69ui@ask.com', NULL, '1', '1990-01-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (38, '745p7p2rrfe', '宜宜', NULL, '俞宜', '13987866129', '745p7p2rrfe@aol.com', NULL, '1', '1995-11-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (39, 'xejhlltccc', '林有', NULL, '许林有', '13379219833', 'xejhlltccc@sohu.com', NULL, '1', '1996-01-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (40, '5ruxdv4', '桂娣', NULL, '曹桂娣', '13536559486', '5ruxdv4@263.net', NULL, '2', '1977-07-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (41, 'wvok02on95e', '阿厚', NULL, '熊厚', '13464138191', 'wvok02on95e@ask.com', NULL, '2', '1987-02-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (42, '63k5cvx', '艺欣', NULL, '邹艺欣', '13512773463', '63k5cvx@sohu.com', NULL, '1', '1973-07-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (43, 'ic6l4q05h9', '阿新', NULL, '康新', '13149746973', 'ic6l4q05h9@qq.com', NULL, '2', '1986-08-07', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (44, 'q5j2qefws', '君琴', NULL, '柳君琴', '13357496986', 'q5j2qefws@aol.com', NULL, '3', '1977-03-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (45, '21aq6t', '致树', NULL, '韩致树', '13344779847', '21aq6t@googlemail.com', NULL, '1', '1988-12-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (46, 'bkny929i', '琴琴', NULL, '西门琴', '13376776493', 'bkny929i@qq.com', NULL, '1', '1977-04-08', 'F', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (47, '3tnorb2', '善厚', NULL, '濮阳善厚', '13669935997', '3tnorb2@aol.com', NULL, '1', '2003-05-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (48, 'w0q8cmus', '育滢', NULL, '施育滢', '13822764859', 'w0q8cmus@ask.com', NULL, '1', '1973-07-08', NULL, '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (49, 'a9gdq3q', '阿中', NULL, '岑中', '13154938926', 'a9gdq3q@googlemail.com', NULL, '3', '1969-01-08', 'M', '2022-06-08 00:00:00', NULL, NULL);
INSERT INTO `user_info` VALUES (50, 'ok3hqncd3wx', '晶妍', NULL, '方晶妍', '13199299967', 'ok3hqncd3wx@0355.net', NULL, '1', '1968-02-08', 'F', '2022-06-08 00:00:00', NULL, NULL);

-- ----------------------------
-- Table structure for ware_info
-- ----------------------------
DROP TABLE IF EXISTS `ware_info`;
CREATE TABLE `ware_info`  (
  `id` bigint NOT NULL,
  `name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `address` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `areacode` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of ware_info
-- ----------------------------

-- ----------------------------
-- Table structure for ware_order_task
-- ----------------------------
DROP TABLE IF EXISTS `ware_order_task`;
CREATE TABLE `ware_order_task`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `order_id` bigint NULL DEFAULT NULL COMMENT '订单编号',
  `consignee` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '收货人',
  `consignee_tel` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '收货人电话',
  `delivery_address` varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '送货地址',
  `order_comment` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单备注',
  `payment_way` varchar(2) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '付款方式 1:在线付款 2:货到付款',
  `task_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '工作单状态',
  `order_body` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '订单描述',
  `tracking_no` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '物流单号',
  `create_time` datetime NULL DEFAULT NULL COMMENT '创建时间',
  `ware_id` bigint NULL DEFAULT NULL COMMENT '仓库编号',
  `task_comment` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '工作单备注',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '库存工作单表 库存工作单表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of ware_order_task
-- ----------------------------

-- ----------------------------
-- Table structure for ware_order_task_detail
-- ----------------------------
DROP TABLE IF EXISTS `ware_order_task_detail`;
CREATE TABLE `ware_order_task_detail`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'sku_id',
  `sku_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'sku名称',
  `sku_num` int NULL DEFAULT NULL COMMENT '购买个数',
  `task_id` bigint NULL DEFAULT NULL COMMENT '工作单编号',
  `refund_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '库存工作单明细表 库存工作单明细表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of ware_order_task_detail
-- ----------------------------

-- ----------------------------
-- Table structure for ware_sku
-- ----------------------------
DROP TABLE IF EXISTS `ware_sku`;
CREATE TABLE `ware_sku`  (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '编号',
  `sku_id` bigint NULL DEFAULT NULL COMMENT 'skuid',
  `warehouse_id` bigint NULL DEFAULT NULL COMMENT '仓库id',
  `stock` int NULL DEFAULT NULL COMMENT '库存数',
  `stock_name` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '存货名称',
  `stock_locked` int NULL DEFAULT NULL COMMENT '锁定库存数',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = 'sku与仓库关联表' ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of ware_sku
-- ----------------------------

-- ----------------------------
-- Table structure for z_log
-- ----------------------------
DROP TABLE IF EXISTS `z_log`;
CREATE TABLE `z_log`  (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `log` varchar(4000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC;

-- ----------------------------
-- Records of z_log
-- ----------------------------

SET FOREIGN_KEY_CHECKS = 1;
