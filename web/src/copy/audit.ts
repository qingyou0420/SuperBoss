export const AUDIT_ACTION_LABEL: Record<string, string> = {
    'agent.card.confirm': '确认入库',
    'agent.soul.write': '修改 SOUL',
    'agent.soul.activate': '启用 SOUL',
    'finance.entry.create': '记一笔',
    'finance.entry.adjust': '调整账目',
    'project.create': '新建项目',
    'project.update': '更新项目',
    'file.delete': '删除文件',
    'file.upload.complete': '完成上传',
    'auth.login': '登录',
    'user.create': '添加成员',
    'user.update': '更新成员',
    'user.password.reset': '重置密码',
    'user.projects.replace': '分配项目',
    'auth.password.change': '修改密码',
    'project.milestones.replace': '更新里程碑',
    'file.download': '下载文件',
    'file.upload.start': '开始上传',
}

export const AUDIT_OBJECT_LABEL: Record<string, string> = {
    finance_entry: '账目',
    agent_card: '卡片',
    project: '项目',
    file: '文件',
    user: '成员',
    folder: '目录',
    knowledge_doc: '文档',
    agent_soul: 'SOUL',
    agent_memory: '记忆',
}

export const AUDIT_OUTCOME_LABEL = {
    SUCCESS: '成功',
    DENIED: '被拒',
} as const

export function auditActionLabel(action: string): string {
    return AUDIT_ACTION_LABEL[action] || action
}

export function auditObjectLabel(objectType: string): string {
    return AUDIT_OBJECT_LABEL[objectType] || objectType
}

export function isKnownAuditAction(action: string): boolean {
    return action in AUDIT_ACTION_LABEL
}
