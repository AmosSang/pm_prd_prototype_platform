# 演示项目 PRD（契约测试样例）

## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | prototype/pages/login.html | page-login |
| 工作台首页 | prototype/pages/index.html | page-home |

## 5 功能需求

### 5.1 登录页 <!-- pa: page-login -->

#### 5.1.1 登录表单 <!-- pa: login-form -->

- 账号输入 <!-- pa: login-account -->：支持手机号 / 邮箱登录
- 验证码输入 <!-- pa: login-captcha -->：6 位数字，支持短信 / 语音获取

#### 5.1.2 登录按钮 <!-- pa: login-submit -->

点击后校验表单，通过则提交登录请求。
