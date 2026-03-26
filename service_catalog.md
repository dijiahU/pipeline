# Self-Hosted Service Catalog for Agent Safety Evaluation

本文档系统性地梳理了可通过 Docker 部署的自托管服务（locally hosted service instances），覆盖 Agent 在真实场景中可能交互的绝大部分服务类型。每个服务均具备 REST API（或等效接口），可作为 Agent 的工具后端，用于安全评测、SFT 数据采集和 GRPO 训练。

> **术语约定：** 本文档中"服务"指可通过 Docker 容器本地部署的、具备 API 接口的自托管 Web 应用（self-hosted web service），Agent 通过 API 而非浏览器与其交互。

---

## 目录

1. [代码与 DevOps](#1-代码与-devops)
2. [项目管理](#2-项目管理)
3. [即时通讯](#3-即时通讯)
4. [文件存储与文档管理](#4-文件存储与文档管理)
5. [数据管理与 BI](#5-数据管理与-bi)
6. [客服与工单](#6-客服与工单)
7. [电子商务](#7-电子商务)
8. [财务与 ERP](#8-财务与-erp)
9. [医疗健康](#9-医疗健康)
10. [人力资源](#10-人力资源)
11. [论坛、Wiki 与知识库](#11-论坛wiki-与知识库)
12. [监控与可观测性](#12-监控与可观测性)
13. [邮件系统](#13-邮件系统)
14. [日程与预约](#14-日程与预约)
15. [CI/CD](#15-cicd)
16. [场景覆盖总结](#16-场景覆盖总结)
17. [资源速查表](#17-资源速查表)

---

## 1. 代码与 DevOps

Agent 在软件工程场景中最常交互的服务类型。核心操作包括代码仓库管理、分支操作、Issue/MR 处理、CI/CD 流水线控制。

### 1.1 GitLab CE

| 属性 | 值 |
|------|-----|
| 官网 | https://about.gitlab.com |
| 仓库 | https://gitlab.com/gitlab-org/gitlab（GitHub 镜像 ~24k stars） |
| Docker 镜像 | `gitlab/gitlab-ce` |
| 协议 | MIT |
| 启动时间 | 3–8 分钟 |
| 最低资源 | 4GB RAM, 2 CPU |
| API 类型 | REST v4, GraphQL |

**API 能力：** Projects, Repositories, Branches, Merge Requests, Issues, Pipelines, CI/CD Jobs, Users/Groups, Wikis, Container Registry, Packages, Deploy Tokens, Webhooks

**安全评测价值：**
- **破坏性操作：** 删除项目/仓库/分支、force-push、删除用户、删除 Container Registry 镜像
- **权限操作：** 修改分支保护规则、变更成员角色、管理 Access Token
- **供应链风险：** 修改 CI/CD pipeline 配置（`.gitlab-ci.yml`）可导致任意代码执行
- **数据泄露：** 导出项目源码、读取私有仓库内容

**备注：** OpenAgentSafety 已使用。启动较重，但功能最全面，是 DevOps 场景的首选。

### 1.2 Gitea

| 属性 | 值 |
|------|-----|
| 官网 | https://about.gitea.com |
| 仓库 | https://github.com/go-gitea/gitea（~46k stars） |
| Docker 镜像 | `gitea/gitea` |
| 协议 | MIT |
| 启动时间 | 10–30 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST（Swagger 文档） |

**API 能力：** Repos, Issues, Pull Requests, Branches, Tags, Releases, Users, Organizations, Teams, Webhooks, Packages

**安全评测价值：**
- **破坏性操作：** 删除仓库/分支/组织、force-push
- **权限操作：** 管理 SSH 密钥、修改 Webhook（可触发外部请求）
- **优势：** 启动极快，资源消耗低，适合 GRPO 环境池中批量部署

**备注：** 功能覆盖 GitLab 的 80%，但资源消耗仅为其 1/16。适合需要大量并行实例的训练场景。

---

## 2. 项目管理

项目管理服务模拟 Agent 作为团队成员处理任务分配、工单管理、迭代规划等场景。

### 2.1 Plane

| 属性 | 值 |
|------|-----|
| 官网 | https://plane.so |
| 仓库 | https://github.com/makeplane/plane（~32k stars） |
| Docker 镜像 | 多容器：`makeplane/plane-frontend`, `plane-backend`, `plane-space` |
| 协议 | AGPL-3.0 |
| 启动时间 | 1–3 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST |

**API 能力：** Workspaces, Projects, Issues（Cycles, Modules, Labels）, Members, Pages, Views, Estimates, Inbox

**安全评测价值：**
- **破坏性操作：** 删除项目/工作区、批量删除 Issue、移除成员
- **权限操作：** 修改项目可见性、变更成员角色
- **数据泄露：** 导出项目数据

**备注：** OpenAgentSafety 已使用。Jira 的开源替代品，Issue 生命周期丰富。

### 2.2 Taiga

| 属性 | 值 |
|------|-----|
| 官网 | https://taiga.io |
| 仓库 | https://github.com/taigaio（~13k stars） |
| Docker 镜像 | 多容器：`taigaio/taiga-back`, `taiga-front`, `taiga-events` |
| 协议 | MPL-2.0 |
| 启动时间 | 1–2 分钟 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Projects, User Stories, Tasks, Sprints, Issues, Epics, Wiki, Milestones, Attachments, Users

**安全评测价值：**
- **破坏性操作：** 删除项目、批量删除 User Story/Task
- **敏捷场景：** Sprint 规划修改、Backlog 重排序
- **数据操作：** 项目导入/导出

---

## 3. 即时通讯

模拟 Agent 在团队沟通场景中发送消息、管理频道、处理通知等操作。涉及隐私（私信访问）和社会工程风险。

### 3.1 Rocket.Chat

| 属性 | 值 |
|------|-----|
| 官网 | https://rocket.chat |
| 仓库 | https://github.com/RocketChat/Rocket.Chat（~41k stars） |
| Docker 镜像 | `rocket.chat`（需配合 MongoDB） |
| 协议 | MIT |
| 启动时间 | 30–90 秒 |
| 最低资源 | 1GB RAM, 2 CPU |
| API 类型 | REST + Realtime (WebSocket) |

**API 能力：** Channels, Groups, DMs, Messages, Users, Roles, Permissions, Integrations, Livechat, Teams, Subscriptions

**安全评测价值：**
- **隐私风险：** 读取私信/私有频道消息、访问用户在线状态
- **社会工程：** 以他人身份发送消息（管理员权限）、创建虚假集成
- **破坏性操作：** 删除频道/消息、删除用户
- **外部交互：** Webhook 集成可触发外部请求

**备注：** OpenAgentSafety 已使用。消息场景的首选。

### 3.2 Mattermost

| 属性 | 值 |
|------|-----|
| 官网 | https://mattermost.com |
| 仓库 | https://github.com/mattermost/mattermost（~31k stars） |
| Docker 镜像 | `mattermost/mattermost-enterprise-edition` |
| 协议 | MIT (Team Edition) |
| 启动时间 | 15–45 秒 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | REST v4 |

**API 能力：** Channels, Teams, Posts, Users, Files, Webhooks, Bots, Plugins, Compliance, LDAP, OAuth Apps

**安全评测价值：**
- **隐私风险：** 读取私有频道、导出合规数据（包含消息历史）
- **破坏性操作：** 删除频道/消息/团队、删除用户
- **凭证管理：** 管理 OAuth 应用、Bot 令牌
- **API 文档质量极高**，工具封装成本低

### 3.3 Zulip

| 属性 | 值 |
|------|-----|
| 官网 | https://zulip.com |
| 仓库 | https://github.com/zulip/zulip（~22k stars） |
| Docker 镜像 | `zulip/docker-zulip` |
| 协议 | Apache-2.0 |
| 启动时间 | 1–3 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST |

**API 能力：** Messages, Streams, Topics, Users, Organizations, Uploads, Reactions, User Groups, Custom Emojis, Alert Words

**安全评测价值：**
- **独特的 topic 模型：** Stream → Topic 两级结构，比 Slack 模型更复杂，可测试 Agent 对层级概念的理解
- **破坏性操作：** 删除消息/Stream、停用用户
- **组织管理：** 修改全局设置、管理用户角色

---

## 4. 文件存储与文档管理

模拟 Agent 处理文件上传/下载、共享链接管理、权限控制等场景。文件操作的不可逆性使其成为安全评测的重要场景。

### 4.1 ownCloud

| 属性 | 值 |
|------|-----|
| 官网 | https://owncloud.com |
| 仓库 | https://github.com/owncloud/core（~8.4k stars） |
| Docker 镜像 | `owncloud/server`（经典版）或 `owncloud/ocis`（Infinite Scale） |
| 协议 | AGPL-3.0 |
| 启动时间 | 30–60 秒（OCIS）/ 1–2 分钟（经典版） |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | WebDAV + OCS REST |

**API 能力：** 文件 CRUD (WebDAV), Sharing（公开链接、用户/组共享）, Users/Groups, Trash, Versions, Capabilities

**安全评测价值：**
- **数据泄露：** 创建无密码公开共享链接、访问他人文件（管理员）
- **破坏性操作：** 删除文件/文件夹、清空回收站（永久删除）
- **权限升级：** 修改共享权限、管理用户账户

**备注：** OpenAgentSafety 已使用。

### 4.2 Nextcloud

| 属性 | 值 |
|------|-----|
| 官网 | https://nextcloud.com |
| 仓库 | https://github.com/nextcloud/server（~28k stars） |
| Docker 镜像 | `nextcloud` |
| 协议 | AGPL-3.0 |
| 启动时间 | 30–90 秒 |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | WebDAV + OCS REST + CalDAV/CardDAV |

**API 能力：** Files (WebDAV), Shares, Users/Groups, Calendars, Contacts, Talk, Activities, Notifications, Apps 管理

**安全评测价值：**
- 与 ownCloud 类似，但生态更大（日历、通讯录、Talk 视频通话）
- **额外风险面：** 安装/卸载应用（可引入任意代码）、修改加密设置、管理安全策略
- **适合跨功能场景：** 文件 + 日历 + 联系人的组合操作

---

## 5. 数据管理与 BI

模拟 Agent 操作数据库记录、管理表结构、执行查询和构建报表。数据操作的批量性和不可逆性带来显著风险。

### 5.1 NocoDB

| 属性 | 值 |
|------|-----|
| 官网 | https://nocodb.com |
| 仓库 | https://github.com/nocodb/nocodb（~50k stars） |
| Docker 镜像 | `nocodb/nocodb` |
| 协议 | AGPL-3.0 |
| 启动时间 | 10–30 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST（自动生成） |

**API 能力：** Tables, Records (CRUD), Fields/Columns, Views, Filters, Sorts, Formulas, Webhooks, Shared Views, API Tokens

**安全评测价值：**
- **极其适合 Agent 交互：** 自动为每张表生成 REST API，CRUD 操作直观
- **破坏性操作：** 删除表/数据库、批量删除记录、删除列（不可逆）
- **数据泄露：** 通过 Shared View 公开数据
- **启动极快、资源极低**，适合大规模并行部署

### 5.2 Directus

| 属性 | 值 |
|------|-----|
| 官网 | https://directus.io |
| 仓库 | https://github.com/directus/directus（~29k stars） |
| Docker 镜像 | `directus/directus` |
| 协议 | BSL-1.1 → GPL-3.0 |
| 启动时间 | 15–45 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST + GraphQL |

**API 能力：** Items (any collection), Collections, Fields, Users, Roles, Permissions, Files, Activity, Webhooks, Flows (自动化), Relations

**安全评测价值：**
- **Schema 级操作：** 不仅能操作数据，还能修改表结构本身
- **自动化风险：** Flows 功能允许创建自动化规则，可能触发意外副作用
- **权限模型复杂：** 细粒度的 Role/Permission 系统，适合测试权限边界

---

## 6. 客服与工单

模拟 Agent 处理客户支持场景：接收工单、分配任务、回复客户、管理 SLA。涉及客户隐私数据和外部通信。

### 6.1 Zammad

| 属性 | 值 |
|------|-----|
| 官网 | https://zammad.org |
| 仓库 | https://github.com/zammad/zammad（~4.5k stars） |
| Docker 镜像 | `ghcr.io/zammad/zammad`（需 docker-compose） |
| 协议 | AGPL-3.0 |
| 启动时间 | 1–3 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST + GraphQL |

**API 能力：** Tickets, Users, Organizations, Groups, Overviews, Tags, Knowledge Base, Channels (Email/Chat), SLAs, Text Modules

**安全评测价值：**
- **客户数据风险：** 访问客户 PII、查看工单中的敏感信息
- **冒充风险：** 以客服身份回复客户、冒充其他 Agent
- **破坏性操作：** 删除/合并工单、删除用户、修改邮件通道配置
- **SLA 篡改：** 修改响应时间规则可导致服务违约
- **工单生命周期丰富：** 创建 → 分配 → 回复 → 升级 → 关闭，路径多样

### 6.2 Chatwoot

| 属性 | 值 |
|------|-----|
| 官网 | https://chatwoot.com |
| 仓库 | https://github.com/chatwoot/chatwoot（~22k stars） |
| Docker 镜像 | `chatwoot/chatwoot` |
| 协议 | MIT |
| 启动时间 | 30–90 秒 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Conversations, Contacts, Messages, Agents, Teams, Inboxes, Labels, Canned Responses, Reports, Webhooks, Automations

**安全评测价值：**
- **多渠道接入：** Email、Live Chat、社交媒体等多入口，操作面广
- **隐私风险：** 访问客户消息历史和联系方式
- **自动化风险：** Automation 规则可触发自动回复/分配
- **外部交互：** Webhook 和 Inbox 配置可连接外部服务

---

## 7. 电子商务

模拟 Agent 管理商品、处理订单、操作退款等电商场景。涉及金融交易和客户数据，安全风险等级高。

### 7.1 Medusa

| 属性 | 值 |
|------|-----|
| 官网 | https://medusajs.com |
| 仓库 | https://github.com/medusajs/medusa（~27k stars） |
| Docker 镜像 | 无单一官方镜像，需基于 Node.js 构建 |
| 协议 | MIT |
| 启动时间 | 15–45 秒 |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Products, Orders, Customers, Carts, Shipping, Payments, Discounts/Promotions, Inventory, Regions, Tax, Gift Cards, Returns/Swaps

**安全评测价值：**
- **金融操作：** 处理退款、修改订单金额、创建虚假折扣
- **客户数据：** 访问 PII 和支付信息
- **库存操作：** 修改库存数量可导致超卖
- **API 文档优秀**，工具封装成本低

### 7.2 Saleor

| 属性 | 值 |
|------|-----|
| 官网 | https://saleor.io |
| 仓库 | https://github.com/saleor/saleor（~21k stars） |
| Docker 镜像 | `ghcr.io/saleor/saleor` |
| 协议 | BSD-3-Clause |
| 启动时间 | 30–60 秒 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | GraphQL（主要接口） |

**API 能力：** Products, Orders, Checkout, Customers, Shipping, Payments, Discounts/Vouchers, Warehouses, Channels, Webhooks, Apps, Staff Permissions

**安全评测价值：**
- **GraphQL 接口：** 与 REST 不同的交互模式，可测试 Agent 对 GraphQL 的理解
- **支付流程：** 取消/退款订单、修改支付配置
- **应用市场：** 安装应用 = 授予 Webhook 访问权限
- **多渠道管理：** Channel 机制增加了操作复杂度

---

## 8. 财务与 ERP

模拟 Agent 处理财务记账、发票管理、库存控制、采购审批等企业核心业务。操作涉及资金流动，风险最高。

### 8.1 ERPNext

| 属性 | 值 |
|------|-----|
| 官网 | https://erpnext.com |
| 仓库 | https://github.com/frappe/erpnext（~22k stars） |
| Docker 镜像 | `frappe/erpnext`（via frappe_docker） |
| 协议 | GPL-3.0 |
| 启动时间 | 3–8 分钟 |
| 最低资源 | 4GB RAM, 2 CPU |
| API 类型 | Frappe REST |

**API 能力：** Accounting（总账、发票、付款）, HR（员工、薪资、假期）, Inventory, Manufacturing, CRM, Projects, Buying, Selling, Assets, Loan Management

**安全评测价值：**
- **金融风险最高：** 创建/修改财务交易、处理付款、修改薪资
- **员工数据：** 访问 PII、薪资、银行账户信息
- **审批流程：** 绕过采购审批、修改税务设置
- **操作面极广：** 覆盖会计、HR、库存、CRM 等多个模块
- **适合高风险场景评测**

### 8.2 Odoo CE

| 属性 | 值 |
|------|-----|
| 官网 | https://odoo.com |
| 仓库 | https://github.com/odoo/odoo（~39k stars） |
| Docker 镜像 | `odoo` |
| 协议 | LGPL-3.0 (CE) |
| 启动时间 | 30–90 秒 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | XML-RPC / JSON-RPC |

**API 能力：** CRM, Sales, Invoicing, Accounting, Inventory, Manufacturing, HR, Purchase, Project, Website, POS, eCommerce

**安全评测价值：**
- **模块化架构：** 可安装/卸载模块（包含任意代码），本身就是安全风险
- **操作面与 ERPNext 相当**，但 API 协议为 JSON-RPC 而非标准 REST
- **启动更快、资源更低**，但 API 不如 ERPNext 直观
- **Server Action：** 可执行任意 Python 代码

---

## 9. 医疗健康

模拟 Agent 操作电子病历、患者数据、处方管理等医疗场景。数据敏感度最高（HIPAA/隐私法规），是安全评测的极端场景。

### 9.1 OpenEMR

| 属性 | 值 |
|------|-----|
| 官网 | https://open-emr.org |
| 仓库 | https://github.com/openemr/openemr（~3k stars） |
| Docker 镜像 | `openemr/openemr` |
| 协议 | GPL-2.0 |
| 启动时间 | 1–3 分钟 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | REST + FHIR R4 |

**API 能力：** Patients, Encounters, Observations, Allergies, Medications, Immunizations, Procedures, Documents, Billing, Scheduling, Facilities, Users

**安全评测价值：**
- **最高数据敏感度：** 患者健康记录（PHI）受 HIPAA 保护
- **破坏性操作：** 删除患者记录、修改处方、篡改计费代码
- **隐私风险：** 访问临床笔记、查看他人诊断信息
- **FHIR API** 是医疗行业标准，可测试 Agent 对行业规范的遵守
- **适合测试 Agent 在高合规要求环境下的行为**

### 9.2 OpenMRS

| 属性 | 值 |
|------|-----|
| 官网 | https://openmrs.org |
| 仓库 | https://github.com/openmrs/openmrs-core（~1.4k stars） |
| Docker 镜像 | `openmrs/openmrs-reference-application` |
| 协议 | MPL-2.0 |
| 启动时间 | 2–5 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST + FHIR |

**API 能力：** Patients, Visits, Encounters, Observations, Concepts, Orders, Drugs, Providers, Locations, Forms, Users

**安全评测价值：**
- **与 OpenEMR 类似的数据敏感度**
- **药物医嘱：** 修改 Drug Orders 可直接影响患者安全
- **面向发展中国家：** 设计适合资源有限的环境，概念模型不同于欧美系统
- **启动较重**（Java/Tomcat），资源消耗较高

---

## 10. 人力资源

模拟 Agent 管理员工信息、考勤、招聘、绩效等 HR 场景。涉及大量员工 PII。

### 10.1 OrangeHRM

| 属性 | 值 |
|------|-----|
| 官网 | https://orangehrm.com |
| 仓库 | https://github.com/orangehrm/orangehrm（~850 stars） |
| Docker 镜像 | `orangehrm/orangehrm` |
| 协议 | GPL-2.0 |
| 启动时间 | 30–60 秒 |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Employees (PIM), Leave, Attendance, Recruitment, Performance, Admin (users, roles), Time Tracking, Directory

**安全评测价值：**
- **员工 PII：** 身份证号、薪资、银行账户等高敏感信息
- **破坏性操作：** 删除员工记录、修改考勤数据
- **薪资风险：** 修改薪资/假期余额
- **招聘数据：** 访问候选人简历和评估信息
- **轻量级**，适合与 ERPNext 搭配覆盖 HR 子场景

---

## 11. 论坛、Wiki 与知识库

模拟 Agent 管理社区内容、编辑文档、处理用户投诉等场景。涉及内容审核和用户管理。

### 11.1 Discourse

| 属性 | 值 |
|------|-----|
| 官网 | https://discourse.org |
| 仓库 | https://github.com/discourse/discourse（~43k stars） |
| Docker 镜像 | `discourse/discourse`（使用 discourse_docker launcher） |
| 协议 | GPL-2.0 |
| 启动时间 | 2–5 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST |

**API 能力：** Topics, Posts, Categories, Users, Groups, Tags, Search, Notifications, Badges, Uploads, Admin Settings, Site Settings

**安全评测价值：**
- **内容审核：** 删除帖子/主题、锁定讨论、隐藏内容
- **用户管理：** 封禁/停用用户、修改信任等级、冒充用户
- **隐私风险：** 访问私信、查看邮箱地址
- **社区治理：** 修改全站设置、调整分类权限

**备注：** Docker 部署方式非标准（使用自有 launcher 而非 docker-compose），集成成本稍高。

### 11.2 MediaWiki

| 属性 | 值 |
|------|-----|
| 官网 | https://mediawiki.org |
| 仓库 | https://github.com/wikimedia/mediawiki（~4.2k stars） |
| Docker 镜像 | `mediawiki` |
| 协议 | GPL-2.0 |
| 启动时间 | 15–45 秒 |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | Action API + REST API |

**API 能力：** Pages（创建/编辑/删除/移动）, Users, Categories, Files/Uploads, Search, Recent Changes, Revisions, User Rights, Blocks, Import/Export

**安全评测价值：**
- **内容篡改：** 编辑/删除页面、修改重要文档内容
- **历史记录操作：** 删除修订历史（隐藏篡改痕迹）
- **权限提升：** 修改用户权限、封禁用户
- **批量操作：** Bot API 允许批量编辑，放大风险
- **维基百科使用的同款软件**，知名度最高

### 11.3 BookStack

| 属性 | 值 |
|------|-----|
| 官网 | https://bookstackapp.com |
| 仓库 | https://github.com/BookStackApp/BookStack（~16k stars） |
| Docker 镜像 | `lscr.io/linuxserver/bookstack`（社区镜像） |
| 协议 | MIT |
| 启动时间 | 15–30 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Books, Chapters, Pages, Shelves, Attachments, Search, Users, Roles, Recycle Bin, Image Gallery

**安全评测价值：**
- **层级文档结构：** Shelf → Book → Chapter → Page 四级结构
- **破坏性操作：** 删除整个 Book（级联删除所有章节和页面）
- **回收站管理：** 可永久删除回收站中的内容
- **轻量启动**，适合知识库管理场景

---

## 12. 监控与可观测性

模拟 Agent 管理告警规则、查看指标、配置仪表板等运维场景。修改告警规则可导致关键事件无法被发现。

### 12.1 Grafana

| 属性 | 值 |
|------|-----|
| 官网 | https://grafana.com |
| 仓库 | https://github.com/grafana/grafana（~66k stars） |
| Docker 镜像 | `grafana/grafana` 或 `grafana/grafana-oss` |
| 协议 | AGPL-3.0 |
| 启动时间 | 5–15 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Dashboards, Data Sources, Alerts, Users/Orgs, Folders, Annotations, API Keys, Service Accounts, Plugins, Notification Channels

**安全评测价值：**
- **告警抑制：** 修改/删除告警规则 → 关键事件无人知晓
- **仪表板破坏：** 删除 Dashboard → 团队失去可见性
- **凭证泄露：** 数据源配置中可能存储数据库密码
- **恶意数据源：** 添加指向恶意服务器的数据源
- **启动极快**

### 12.2 Prometheus

| 属性 | 值 |
|------|-----|
| 官网 | https://prometheus.io |
| 仓库 | https://github.com/prometheus/prometheus（~57k stars） |
| Docker 镜像 | `prom/prometheus` |
| 协议 | Apache-2.0 |
| 启动时间 | 5–10 秒 |
| 最低资源 | 512MB RAM, 1 CPU |
| API 类型 | HTTP Query API |

**API 能力：** Query (PromQL), Targets, Rules, Alerts, Series, Labels, Metadata, Admin（TSDB snapshot, delete series）

**安全评测价值：**
- **主要为只读：** 查询 API 本身风险较低
- **Admin API：** 如果启用，可删除时间序列数据、创建 TSDB 快照
- **信息暴露：** 指标数据可揭示基础设施拓扑和内部服务信息
- **通常与 Grafana 配合使用**

---

## 13. 邮件系统

模拟 Agent 管理邮件账户、发送邮件等场景。邮件发送是典型的不可撤回操作。

### 13.1 Mailu

| 属性 | 值 |
|------|-----|
| 官网 | https://mailu.io |
| 仓库 | https://github.com/Mailu/Mailu（~6k stars） |
| Docker 镜像 | 多容器：`ghcr.io/mailu/front`, `admin`, `dovecot`, `postfix` 等 |
| 协议 | MIT |
| 启动时间 | 1–3 分钟 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | Admin REST |

**API 能力：** Domains, Users, Aliases, Relays, Alternative Domains, Quota Management

**安全评测价值：**
- **不可撤回操作：** 发送邮件后无法收回
- **邮件转发：** 修改别名可将他人邮件转发到指定地址
- **账户管理：** 创建/删除邮箱、修改密码
- **域名管理：** 删除域名会删除该域下所有邮箱

### 13.2 Mailcow

| 属性 | 值 |
|------|-----|
| 官网 | https://mailcow.email |
| 仓库 | https://github.com/mailcow/mailcow-dockerized（~9k stars） |
| Docker 镜像 | 多容器（10+），via docker-compose |
| 协议 | GPL-3.0 |
| 启动时间 | 2–5 分钟 |
| 最低资源 | 2GB RAM, 2 CPU |
| API 类型 | REST |

**API 能力：** Domains, Mailboxes, Aliases, DKIM, Quarantine, Logs, Fail2ban, Rate Limits, Transport, TLS Policy

**安全评测价值：**
- **DKIM 管理：** 修改 DKIM 密钥可用于邮件伪造
- **隔离区操作：** 释放或删除隔离邮件
- **日志访问：** 邮件元数据可泄露通信模式
- **比 Mailu 功能更全**，但部署更重

---

## 14. 日程与预约

模拟 Agent 管理会议预约、日程安排等场景。涉及取消他人会议等社交影响操作。

### 14.1 Cal.com

| 属性 | 值 |
|------|-----|
| 官网 | https://cal.com |
| 仓库 | https://github.com/calcom/cal.com（~33k stars） |
| Docker 镜像 | `calcom/cal.com`（via docker-compose） |
| 协议 | AGPL-3.0 |
| 启动时间 | 30–90 秒 |
| 最低资源 | 1GB RAM, 1 CPU |
| API 类型 | REST v1 + v2 |

**API 能力：** Bookings, Event Types, Availability, Schedules, Teams, Users, Webhooks, Workflows, Apps/Integrations

**安全评测价值：**
- **社交影响：** 取消/删除他人的预约
- **可用性篡改：** 修改日程可用性导致无法被预约
- **隐私风险：** 访问参会者个人信息
- **自动化风险：** Webhook 和 Workflow 可触发外部请求
- **Calendly 的开源替代**，现代 API 设计

---

## 15. CI/CD

模拟 Agent 管理构建流水线、部署任务、密钥管理等 DevOps 场景。CI/CD 系统天然具备代码执行能力，安全风险极高。

### 15.1 Woodpecker CI

| 属性 | 值 |
|------|-----|
| 官网 | https://woodpecker-ci.org |
| 仓库 | https://github.com/woodpecker-ci/woodpecker（~4.3k stars） |
| Docker 镜像 | `woodpeckerci/woodpecker-server` + `woodpecker-agent` |
| 协议 | Apache-2.0 |
| 启动时间 | 5–15 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Repos, Pipelines, Steps, Logs, Users, Secrets, Registries, Cron Jobs, Agents

**安全评测价值：**
- **代码执行：** 触发 Pipeline = 在 Agent 容器内执行任意命令
- **密钥泄露：** 访问/修改 Secrets（数据库密码、API Key 等）
- **供应链攻击：** 修改 Pipeline 配置、篡改构建产物
- **Registry 凭证：** 管理容器镜像仓库的登录凭证
- **极轻量**，适合与 Gitea 搭配

### 15.2 Drone

| 属性 | 值 |
|------|-----|
| 官网 | https://drone.io |
| 仓库 | https://github.com/harness/drone（~32k stars） |
| Docker 镜像 | `drone/drone` + `drone/drone-runner-docker` |
| 协议 | Apache-2.0 (Community) |
| 启动时间 | 5–15 秒 |
| 最低资源 | 256MB RAM, 1 CPU |
| API 类型 | REST |

**API 能力：** Repos, Builds, Logs, Secrets, Cron, Users, Templates

**安全评测价值：**
- **与 Woodpecker 类似**（Woodpecker 是 Drone 的社区 fork）
- **Promote to Production：** 将构建提升到生产环境
- **密钥管理和 Pipeline 执行** 同样是高风险操作

---

## 16. 场景覆盖总结

### 16.1 Agent 常见应用场景 → 可用服务映射

| Agent 应用场景 | 服务 | 覆盖状态 |
|---------------|------|---------|
| 软件开发 | GitLab, Gitea | ✅ 完全覆盖 |
| 项目管理 | Plane, Taiga | ✅ 完全覆盖 |
| 团队沟通 | Rocket.Chat, Mattermost, Zulip | ✅ 完全覆盖 |
| 文件管理 | ownCloud, Nextcloud | ✅ 完全覆盖 |
| 数据库/表格 | NocoDB, Directus | ✅ 完全覆盖 |
| 客户服务 | Zammad, Chatwoot | ✅ 完全覆盖 |
| 电子商务 | Medusa, Saleor | ✅ 完全覆盖 |
| 财务会计 | ERPNext, Odoo | ✅ 完全覆盖 |
| 医疗健康 | OpenEMR, OpenMRS | ✅ 完全覆盖 |
| 人力资源 | OrangeHRM | ✅ 基本覆盖 |
| 内容管理/社区 | Discourse, MediaWiki, BookStack | ✅ 完全覆盖 |
| 运维监控 | Grafana, Prometheus | ✅ 完全覆盖 |
| 邮件通信 | Mailu, Mailcow | ✅ 完全覆盖 |
| 日程预约 | Cal.com | ✅ 基本覆盖 |
| CI/CD 部署 | Woodpecker, Drone | ✅ 完全覆盖 |
| IoT/硬件控制 | — | ❌ 不适用（非 Web 服务） |
| 实时音视频 | — | ❌ 不适用（需要流媒体） |

### 16.2 安全风险维度 → 可评测服务

| 风险维度 | 最佳评测服务 | 原因 |
|---------|-------------|------|
| 数据删除/破坏 | GitLab, NocoDB, ERPNext | 丰富的删除操作 |
| 隐私/PII 泄露 | OpenEMR, OrangeHRM, Zammad | 高敏感个人数据 |
| 金融风险 | ERPNext, Odoo, Medusa | 涉及资金流动 |
| 供应链攻击 | Woodpecker, Drone, GitLab | CI/CD = 代码执行 |
| 社会工程/冒充 | Rocket.Chat, Discourse, Mailu | 以他人身份通信 |
| 权限提升 | Directus, GitLab, MediaWiki | 细粒度权限模型 |
| 不可撤回操作 | Mailu, Medusa (退款), Cal.com | 操作后无法回退 |
| 配置篡改 | Grafana, Prometheus, Mailcow | 修改后果隐蔽但严重 |

### 16.3 推荐最小组合

如果资源有限，以下 **6 个服务** 即可覆盖核心安全评测场景：

| 服务 | 覆盖场景 | 资源需求 |
|------|---------|---------|
| **Gitea** | 代码管理 + 轻量 DevOps | 256MB |
| **Plane** | 项目管理 | 2GB |
| **Mattermost** | 团队沟通 | 1GB |
| **NocoDB** | 数据操作 | 256MB |
| **ERPNext** | 财务 + HR + CRM | 4GB |
| **OpenEMR** | 医疗（高敏感数据） | 1GB |
| **合计** | | **~8.5GB RAM** |

如果替换 Gitea 为 GitLab，合计约 **12GB RAM**。

---

## 17. 资源速查表

| 服务 | 最低 RAM | 启动时间 | GitHub Stars | Docker 镜像 |
|------|---------|---------|-------------|-------------|
| GitLab CE | 4GB | 3–8 min | ~24k | `gitlab/gitlab-ce` |
| Gitea | 256MB | 10–30s | ~46k | `gitea/gitea` |
| Plane | 2GB | 1–3 min | ~32k | `makeplane/*` |
| Taiga | 1GB | 1–2 min | ~13k | `taigaio/*` |
| Rocket.Chat | 1GB | 30–90s | ~41k | `rocket.chat` |
| Mattermost | 1GB | 15–45s | ~31k | `mattermost/*` |
| Zulip | 2GB | 1–3 min | ~22k | `zulip/docker-zulip` |
| ownCloud | 512MB | 30–60s | ~8.4k | `owncloud/server` |
| Nextcloud | 512MB | 30–90s | ~28k | `nextcloud` |
| NocoDB | 256MB | 10–30s | ~50k | `nocodb/nocodb` |
| Directus | 256MB | 15–45s | ~29k | `directus/directus` |
| Zammad | 2GB | 1–3 min | ~4.5k | `ghcr.io/zammad/zammad` |
| Chatwoot | 1GB | 30–90s | ~22k | `chatwoot/chatwoot` |
| Medusa | 512MB | 15–45s | ~27k | 需自行构建 |
| Saleor | 1GB | 30–60s | ~21k | `ghcr.io/saleor/saleor` |
| ERPNext | 4GB | 3–8 min | ~22k | `frappe/erpnext` |
| Odoo CE | 1GB | 30–90s | ~39k | `odoo` |
| OpenEMR | 1GB | 1–3 min | ~3k | `openemr/openemr` |
| OpenMRS | 2GB | 2–5 min | ~1.4k | `openmrs/*` |
| OrangeHRM | 512MB | 30–60s | ~850 | `orangehrm/orangehrm` |
| Discourse | 2GB | 2–5 min | ~43k | `discourse/discourse` |
| MediaWiki | 512MB | 15–45s | ~4.2k | `mediawiki` |
| BookStack | 256MB | 15–30s | ~16k | `lscr.io/linuxserver/bookstack` |
| Grafana | 256MB | 5–15s | ~66k | `grafana/grafana` |
| Prometheus | 512MB | 5–10s | ~57k | `prom/prometheus` |
| Mailu | 1GB | 1–3 min | ~6k | `ghcr.io/mailu/*` |
| Mailcow | 2GB | 2–5 min | ~9k | docker-compose |
| Cal.com | 1GB | 30–90s | ~33k | `calcom/cal.com` |
| Woodpecker CI | 256MB | 5–15s | ~4.3k | `woodpeckerci/*` |
| Drone | 256MB | 5–15s | ~32k | `drone/drone` |
