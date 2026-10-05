# 原生业务工具提交示例

以下示例均先回显理解、核实归属并读取当前配置；已在目标归属则跳过空间导航。示例中的短引用是说明位置的占位值，执行必须替换为真实工具回执；revision取最近回执，no-op可不增长。搜索和分页按真实缺口执行，不按示例次数机械调用。所有finish均仅结构检查，模型对照原需求后总结并交接“命名并保存”页面的手动发送预览和保存。

## 固定群通知

在数据中心-组织空间，每月1日9点给BI测试群发送指定斜体文字、链接并@所有人。

先回显；当前缺 space.list 时先 ui.open_space_page，再定位并打开指定空间、get_context。时间及收件人均已确定可成批提交；search_options(kind=recipient_group,query=BI测试群) 唯一精确候选返回 group-bi。不要访问正文外链。

```json
[
  {
    "action": "space.message_rule.start_draft",
    "arguments": {
      "mode": "blank",
      "operations": [
        {
          "operation": "set_schedule",
          "frequency": "monthly",
          "monthDays": [
            1
          ],
          "times": [
            "09:00"
          ]
        },
        {
          "operation": "set_send_rule",
          "sendRule": "scheduled_no_dataset"
        }
      ],
      "scene": "msg-notify"
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 2,
      "operations": [
        {
          "operation": "set_recipients",
          "groupRefs": [
            "group-bi"
          ]
        },
        {
          "operation": "set_content",
          "title": "7月数码3C标准更新-第四期",
          "components": [
            {
              "type": "richtext",
              "segments": [
                {
                  "type": "mention-all"
                },
                {
                  "type": "newline"
                },
                {
                  "type": "text",
                  "text": "7月数码3C标准更新-第四期：游戏机、游戏手柄与电子书。",
                  "italic": true
                },
                {
                  "type": "newline"
                },
                {
                  "type": "link",
                  "text": "7月数码3C标准更新-第四期",
                  "url": "https://atrenew.feishu.cn/wiki/KHSow4bRBioh3yk3BO4cFtoXnQg"
                }
              ]
            }
          ]
        },
        {
          "operation": "set_finalize",
          "ruleName": "数码3C标准更新通知"
        }
      ],
      "finish": true
    }
  }
]
```

## 仪表盘截图、AI与跳转

每天9点和18点给本人发送数巢仪表盘模板截图、AI解读和跳转。

回显并核对归属。search_options dashboard 找到 dashboard-orders，再查该父对象 widget；widget-ai 的真实 contentKind=ai_interpret。个人接收人由原生锁定本人，无需重搜。

```json
[
  {
    "action": "space.message_rule.start_draft",
    "arguments": {
      "mode": "blank",
      "operations": [
        {
          "operation": "set_schedule",
          "frequency": "daily",
          "times": [
            "09:00",
            "18:00"
          ]
        },
        {
          "operation": "set_send_rule",
          "sendRule": "scheduled_no_dataset"
        }
      ],
      "scene": "dashboard-push"
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 2,
      "operations": [
        {
          "operation": "set_content",
          "title": "数巢仪表盘订阅",
          "components": [
            {
              "type": "dashboard-screenshot",
              "dashboardRef": "dashboard-orders"
            },
            {
              "type": "dashboard-chart",
              "widgetRef": "widget-ai"
            },
            {
              "type": "button",
              "text": "打开仪表盘",
              "actionType": "dashboard",
              "dashboardRef": "dashboard-orders"
            }
          ]
        },
        {
          "operation": "set_finalize",
          "ruleName": "数巢仪表盘早晚报"
        }
      ],
      "finish": true
    }
  }
]
```

## 昨日四列数据表

每周一14点将指定数据集昨日区经、门店、成交量、金额汇为一张卡片发给本人。

先回显归属、粒度与四列要求并填时间。指定数据集定向搜索，field查询合并全部字段关键词，确认回收成交日期口径。查询回执给 query-orders 及 output-manager/store/volume/amount。示例分两批便于展示真实输出；指标有outputKey时可以同批使用local引用。

```json
[
  {
    "action": "space.message_rule.start_draft",
    "arguments": {
      "mode": "blank",
      "operations": [
        {
          "operation": "set_schedule",
          "frequency": "weekly",
          "weekdays": [
            1
          ],
          "times": [
            "14:00"
          ]
        },
        {
          "operation": "set_send_rule",
          "sendRule": "scheduled_dataset"
        }
      ],
      "scene": "data-alert"
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 2,
      "operations": [
        {
          "operation": "upsert_dataset_query",
          "datasetRef": "dataset-orders",
          "dimensionRefs": [
            "field-manager",
            "field-store"
          ],
          "metrics": [
            {
              "fieldRef": "field-volume"
            },
            {
              "fieldRef": "field-amount"
            }
          ],
          "filters": [
            {
              "fieldRef": "field-date",
              "operator": "eq",
              "valueExp": "last_day",
              "values": []
            }
          ]
        }
      ]
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 3,
      "operations": [
        {
          "operation": "set_push_mode",
          "mode": "all"
        },
        {
          "operation": "set_content",
          "title": "昨日成交",
          "components": [
            {
              "type": "data-table",
              "queryRef": "query-orders",
              "outputRefs": [
                "output-manager",
                "output-store",
                "output-volume",
                "output-amount"
              ]
            }
          ]
        },
        {
          "operation": "set_finalize",
          "ruleName": "昨日成交周报"
        }
      ],
      "finish": true
    }
  }
]
```

## 原值及日环比预警

每天10点，仪表盘昨日成交量<10000且日环比<-0.1时发给本人，正文包含两项具体值。

先回显并配置时间/conditional。dashboard下alert_widget找到真实指标并import_widget_source到当前未绑定任务（已有快捷草稿直接接续）。本例假定导入回执revision=3，query-sales、field-volume、field-date是实际原值/日期引用，完整输出确认缺日环比；原有其他筛选也须保留，示例中只有日期筛选。

```json
[
  {
    "action": "space.message_rule.start_draft",
    "arguments": {
      "mode": "blank",
      "operations": [
        {
          "operation": "set_schedule",
          "frequency": "daily",
          "times": [
            "10:00"
          ]
        },
        {
          "operation": "set_send_rule",
          "sendRule": "conditional"
        }
      ],
      "scene": "data-alert"
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 2,
      "operations": [
        {
          "operation": "import_widget_source",
          "widgetRef": "alert-widget-sales"
        }
      ]
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 3,
      "operations": [
        {
          "operation": "upsert_dataset_query",
          "queryRef": "query-sales",
          "metricsMode": "merge",
          "metrics": [
            {
              "fieldRef": "field-volume"
            },
            {
              "fieldRef": "field-volume",
              "contrast": {
                "timeFieldRef": "field-date",
                "valueExp": "last_day",
                "calcMethod": "dayChain",
                "calcType": "diffRate"
              }
            }
          ],
          "filters": [
            {
              "fieldRef": "field-date",
              "operator": "eq",
              "valueExp": "last_day",
              "values": []
            }
          ]
        }
      ]
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 4,
      "operations": [
        {
          "operation": "set_trigger_conditions",
          "queryRef": "query-sales",
          "conditionGroups": [
            {
              "conditions": [
                {
                  "outputRef": "output-volume",
                  "operator": "lt",
                  "values": [
                    10000
                  ]
                },
                {
                  "outputRef": "output-dod",
                  "operator": "lt",
                  "values": [
                    -0.1
                  ]
                }
              ]
            }
          ]
        },
        {
          "operation": "set_push_mode",
          "mode": "all"
        },
        {
          "operation": "set_content",
          "title": "昨日成交预警",
          "components": [
            {
              "type": "richtext",
              "markdown": "昨日成交异常请关注。昨日成交量为$$volume$$，日环比为$$dod$$。",
              "bindings": [
                {
                  "key": "volume",
                  "refType": "dataset_field",
                  "refSubType": "field",
                  "queryRef": "query-sales",
                  "outputRefs": [
                    "output-volume"
                  ]
                },
                {
                  "key": "dod",
                  "refType": "dataset_field",
                  "refSubType": "field",
                  "queryRef": "query-sales",
                  "outputRefs": [
                    "output-dod"
                  ]
                }
              ]
            }
          ]
        },
        {
          "operation": "set_finalize",
          "ruleName": "昨日成交异常预警"
        }
      ],
      "finish": true
    }
  }
]
```

## 异常订单与动态负责人

明天11点检查昨日交易失败且提交订单额>8000的小订单，发给对应区经及战区负责人；标题带昨日变量，红色加粗文字与四列表格。

回显并核对空间。executeAt按用户时区和当前真实日期换算；这里2026-09-18仅是示例，不能照抄。一次搜索订单/日期/状态/门店/金额/两种负责人，enum_value核验交易失败。已证实每条查询输出对应一个小订单、金额不重复累加，负责人具oa/ob资格并加入维度。现有输出来自实际查询；无法证实粒度须业务澄清。

```json
[
  {
    "action": "space.message_rule.start_draft",
    "arguments": {
      "mode": "blank",
      "operations": [
        {
          "operation": "set_schedule",
          "frequency": "once",
          "executeAt": "2026-09-18 11:00"
        },
        {
          "operation": "set_send_rule",
          "sendRule": "conditional"
        }
      ],
      "scene": "data-alert"
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 2,
      "operations": [
        {
          "operation": "upsert_dataset_query",
          "datasetRef": "dataset-orders",
          "dimensionRefs": [
            "field-order",
            "field-store",
            "field-manager",
            "field-region-lead"
          ],
          "metrics": [
            {
              "fieldRef": "field-amount"
            }
          ],
          "filters": [
            {
              "fieldRef": "field-date",
              "operator": "eq",
              "valueExp": "last_day",
              "values": []
            },
            {
              "fieldRef": "field-status",
              "operator": "in",
              "valueRefs": [
                "value-trade-failed"
              ]
            }
          ]
        }
      ]
    }
  },
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 3,
      "operations": [
        {
          "operation": "set_trigger_conditions",
          "queryRef": "query-orders",
          "conditionGroups": [
            {
              "conditions": [
                {
                  "outputRef": "output-amount",
                  "operator": "gt",
                  "values": [
                    8000
                  ]
                }
              ]
            }
          ]
        },
        {
          "operation": "set_push_mode",
          "mode": "record",
          "queryRef": "query-orders"
        },
        {
          "operation": "set_recipients",
          "fieldRecipients": [
            {
              "queryRef": "query-orders",
              "fieldRef": "field-manager"
            },
            {
              "queryRef": "query-orders",
              "fieldRef": "field-region-lead"
            }
          ]
        },
        {
          "operation": "set_content",
          "title": "$$yesterday$$未成交大额订单",
          "titleBindings": [
            {
              "key": "yesterday",
              "refType": "system_time",
              "refSubType": "yesterday"
            }
          ],
          "components": [
            {
              "type": "richtext",
              "segments": [
                {
                  "type": "text",
                  "text": "昨日未成交的大额订单如下：",
                  "bold": true,
                  "color": "#ff0000"
                }
              ]
            },
            {
              "type": "data-table",
              "queryRef": "query-orders",
              "outputRefs": [
                "output-order",
                "output-store",
                "output-manager",
                "output-amount"
              ]
            }
          ]
        },
        {
          "operation": "set_finalize",
          "ruleName": "昨日未成交大额订单"
        }
      ],
      "finish": true
    }
  }
]
```

## 只把已有查询日期改为昨日

已有回读显示该查询 filtersComplete=true，configuration 中第 0 条为成交日期，第 1 条是门店范围。使用当前 revision 和第 0 条实际 fieldRef；不传 filters/metrics，也不需要重查全部字段。

```json
[
  {
    "action": "space.message_rule.apply_draft",
    "arguments": {
      "expectedRevision": 3,
      "operations": [
        {
          "operation": "upsert_dataset_query",
          "queryRef": "query-orders",
          "filterEdits": [
            {
              "action": "replace",
              "filterIndex": 0,
              "fieldRef": "field-order-date",
              "filter": {
                "operator": "eq",
                "valueExp": "last_day",
                "values": []
              }
            }
          ]
        }
      ]
    }
  }
]
```

索引按修改前回读定位，保留门店筛选、参数、原值及同环比输出。若还需新增同环比且缺少合法组合，单次 get_context({includeComparisonCapabilities:true}) 后补齐；已有比较直接复用。
