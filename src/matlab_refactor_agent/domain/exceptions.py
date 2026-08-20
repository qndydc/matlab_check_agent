"""
Description: 定义项目、配置、artifact、Worker、Agent、冲突和质量门禁异常层级。
References: Python Exception。
Referenced By: 全部能力层、基础设施层、调度层和 CLI。
"""

class MatlabRefactorError(Exception):
    """作用：统一可预期业务异常；输入：错误消息；输出：可由接口层捕获的异常；数据流：能力/基础设施层 -> CLI。"""


class ProjectPathError(MatlabRefactorError):
    """作用：报告无效项目路径；输入：路径检查错误；输出：业务异常；数据流：扫描器 -> CLI。"""


class ParserUnavailableError(MatlabRefactorError):
    """作用：报告解析依赖不可用；输入：导入错误；输出：业务异常；数据流：解析器初始化 -> CLI。"""


class ConfigurationError(MatlabRefactorError):
    """作用：报告配置读取或校验失败；输入：环境变量/模型错误；输出：业务异常；数据流：配置加载 -> CLI。"""


class OrchestrationError(MatlabRefactorError):
    """作用：报告调度流水线失败；输入：任务或 Worker 错误；输出：业务异常；数据流：Orchestrator -> CLI。"""


class WorkerNotFoundError(OrchestrationError):
    """作用：报告 Worker 未注册；输入：Worker 类型；输出：调度异常；数据流：WorkerPool -> Orchestrator。"""


class LLMClientError(OrchestrationError):
    """作用：报告 LLM 请求、空响应或结构化校验失败；输入：SDK/响应错误；输出：不泄露密钥的业务异常。"""


class LLMOutputTruncatedError(LLMClientError):
    """作用：标识模型输出达到 token 上限；输入：finish_reason=length；输出：允许编排器拆簇重提的异常信号。"""


class QualityGateError(OrchestrationError):
    """作用：报告阶段输出不满足质量门禁；输入：校验错误；输出：调度异常；数据流：QualityGate -> Orchestrator。"""


class ArtifactError(OrchestrationError):
    """作用：报告 artifact 读写或路径越界；输入：artifact 操作错误；输出：调度异常；数据流：ArtifactStore -> Worker/Orchestrator。"""


class ConflictError(OrchestrationError):
    """作用：报告多个任务的资源声明冲突；输入：冲突路径和任务；输出：调度异常；数据流：ConflictResolver -> Orchestrator。"""


class ChangeSetExecutionError(OrchestrationError):
    """作用：报告隔离复制、路径改写或源树完整性失败；输入：执行错误；输出：安全中止。"""
