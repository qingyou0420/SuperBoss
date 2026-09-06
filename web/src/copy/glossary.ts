export const ROLE_LABEL = {
    OWNER: '老板',
    MANAGER: '管理层',
    STAFF: '员工',
} as const

export const STAGE_LABEL = {
    PLANNING: '筹备',
    ACTIVE: '进行',
    DELIVERING: '交付',
    REVIEW: '复盘',
    ARCHIVED: '归档',
} as const

export const ACCOUNT_STATUS_LABEL = {
    ACTIVE: '正常',
    DISABLED: '已禁用',
} as const

export const FILE_STATE_LABEL = {
    UPLOADING: '处理中',
    QUARANTINED: '处理中',
    SCANNING: '处理中',
    CLEAN: '',
    INFECTED: '未通过',
    FAILED: '未通过',
} as const

export const FINANCE_KIND_LABEL = {
    COST: '成本',
    INCOME: '收入',
} as const

export const FINANCE_SCOPE_LABEL = {
    COMPANY: '公司运营',
    PROJECT: '项目',
} as const

export const VISIBILITY_LABEL = {
    ALL: '全员',
    MANAGEMENT: '管理层',
    OWNER_ONLY: '仅自己',
} as const

export const CARD_KIND_LABEL = {
    finance_entry: '记一笔',
    finance_adjust: '调整账目',
    project_create: '新建项目',
    project_update: '更新项目',
    milestone_change: '里程碑',
    file_move: '移动文件',
    memory: '记住',
    knowledge_ingest: '知识入库',
} as const

export const CARD_ERROR_LABEL: Record<string, string> = {
    FINANCE_PROJECT_NOT_FOUND: '项目不存在',
    PROJECT_NAME_CONFLICT: '同名项目已存在',
    PROJECT_NOT_FOUND: '项目不存在',
    CARD_COMMIT_FAILED: '入库失败',
    FOLDER_FORBIDDEN: '没有权限',
    FILE_NOT_FOUND: '文件不存在',
}

export const CARD_STATUS_LABEL = {
    PROPOSED: '待确认',
    CONFIRMED: '入库中',
    COMMITTED: '已入库',
    REVISED: '已改写',
    REJECTED: '已放弃',
    FAILED: '入库失败',
} as const

export const MEMORY_KIND_LABEL = {
    FACT: '事实',
    PREFERENCE: '偏好',
    DECISION: '决定',
    PROJECT_NOTE: '项目备注',
    DAILY_DIGEST: '每日纪要',
} as const

export const DOC_STATUS_LABEL = {
    DRAFT: '草稿',
    PUBLISHED: '已发布',
} as const

export const FOLDER_NAME = {
    OWNER_PRIVATE: '老板私有',
    PROJECTS: '项目',
    COMPANY: '公司',
} as const

export const FIELD_LABEL = {
    category: '类别',
    amount: '金额',
    amountYuan: '金额（元）',
    kind: '类型',
    scope: '范围',
    project: '项目',
    visibility: '可见范围',
    visibilityWillBecome: '可见范围将变为',
    name: '名称',
    stage: '阶段',
    title: '标题',
    due: '到期',
    date: '日期',
    memo: '备注',
    content: '内容',
    file: '文件',
    folder: '目录',
    targetFolder: '目标目录',
    document: '文档',
    starts: '开始',
    description: '说明',
} as const
