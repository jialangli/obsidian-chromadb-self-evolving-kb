---
name: ❓ 问题求助
description: 使用过程中遇到问题需要帮助
title: "[Question] "
labels: ["question"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        请先搜索已有 Issue 和文档，确认你的问题还没有被解答过。

  - type: textarea
    id: question
    attributes:
      label: 你的问题
      description: 详细描述你遇到的问题。
    validations:
      required: true

  - type: textarea
    id: context
    attributes:
      label: 环境信息
      description: 你的运行环境（Python 版本、操作系统、配置等）。
      value: |
        - Python 版本：
        - 操作系统：
        - 安装方式：pip install / git clone
        - 配置文件（脱敏后）：
    validations:
      required: false

  - type: textarea
    id: tried
    attributes:
      label: 你已经尝试过什么？
      description: 描述你已经做了哪些排查或尝试。
    validations:
      required: false
