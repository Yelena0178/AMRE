# 测试数据

按模块存放确定性的输入与预期结果。`config/layered.yaml` 用于验证配置优先级与相对路径。
`research/contract.yaml` 是合同写入及错误路径测试的合成输入。
通过 `fixture_dir` 夹具定位本目录。需要修改输入时先复制到 pytest 的临时工作区。
