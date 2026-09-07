---
name: 🐛 Bug 报告
description: 提交一个 Bug 帮助我们改进
title: "[Bug] "
labels: ["bug"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        感谢你花时间提交 Bug 报告！请尽量详细描述问题，帮助我们快速定位和修复。

  - type: textarea
    id: description
    attributes:
      label: 问题描述
      description: 清晰简洁地描述这个 Bug 是什么。
      placeholder: 当我执行 XXX 操作时，出现了 XXX 错误...
    validations:
      required: true

  - type: textarea
    id: reproduce
    attributes:
      label: 复现步骤
      description: 详细描述如何复现这个问题。
      placeholder: |
        1. 执行命令 '...'
        2. 点击 '...'
        3. 看到错误 '...'
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: 预期行为
      description: 你认为正确的行为应该是什么。
    validations:
      required: true

  - type: textarea
    id: actual
    attributes:
      label: 实际行为
      description: 实际发生了什么。如果有错误日志，请完整粘贴。
    validations:
      required: true

  - type: input
    id: python_version
    attributes:
      label: Python 版本
      placeholder: e.g. 3.11.4
    validations:
      required: false

  - type: input
    id: os_version
    attributes:
      label: 操作系统
      placeholder: e.g. Windows 11 / macOS 14 / Ubuntu 22.04
    validations:
      required: false

  - type: input
    id: version
    attributes:
      label: 项目版本
      description: 提交哈希或版本号。
      placeholder: e.g. v0.1.0 / abc1234
    validations:
      required: false

  - type: textarea
    id: additional
    attributes:
      label: 其他信息
      description: 任何其他可能有帮助的信息（截图、配置文件等）。
    validations:
      required: false
