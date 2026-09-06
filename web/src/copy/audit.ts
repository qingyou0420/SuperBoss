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
}

export const AUDIT_OUTCOME_LABEL = {
    SUCCESS: '成功',
    DENIED: '被拒',
} as const

export function auditActionLabel(action: string): string {
    return AUDIT_ACTION_LABEL[action] || action
}
