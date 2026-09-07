---
name: 💡 功能建议
description: 提出新功能或改进建议
title: "[Feature] "
labels: ["enhancement"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        感谢你的建议！请描述清楚你想要的功能以及为什么需要它。

  - type: textarea
    id: problem
    attributes:
      label: 你遇到了什么问题？
      description: 请描述这个功能建议是为了解决什么问题。
      placeholder: 我总是因为...而感到困扰...
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: 你期望的解决方案是什么？
      description: 清晰描述你希望如何实现。
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: 你考虑过其他替代方案吗？
      description: 如果你想到了其他实现方式，也可以写在这里。
    validations:
      required: false

  - type: textarea
    id: additional
    attributes:
      label: 补充信息
      description: 任何其他相关信息、截图或参考资料。
    validations:
      required: false
