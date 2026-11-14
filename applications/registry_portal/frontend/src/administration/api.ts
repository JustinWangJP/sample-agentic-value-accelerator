import { hasStrings, isObject } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import type { ApiRequest } from '../api/request'
import { AWS_RECORD_STATUSES, type RecordStatus } from '../drafts/api'

/** 一覧の management クエリ。バックエンドの ManagementFilter（domain/administration.py）と揃える */
export const MANAGEMENT_FILTERS = ['unmanaged', 'managed'] as const
export type ManagementFilter = (typeof MANAGEMENT_FILTERS)[number]
/** 観測した認可方式。バックエンドの AuthorizerType（domain/administration.py）と揃える */
export const AUTHORIZER_TYPES = ['CUSTOM_JWT', 'AWS_IAM'] as const
export type AuthorizerType = (typeof AUTHORIZER_TYPES)[number]
/** 自動承認の観測結果。バックエンドの AutoApprovalState（domain/submissions.py）と揃える */
export const AUTO_APPROVAL_STATES = ['DISABLED', 'ENABLED', 'UNKNOWN'] as const
export type AutoApprovalState = (typeof AUTO_APPROVAL_STATES)[number]

function isOneOf<T>(values: readonly T[], value: unknown): value is T {
  return values.some((item) => item === value)
}

export type AdminRecord = {
  recordId: string
  name: string
  displayName: string
  recordType: CatalogRecordType
  recordVersion: string
  status: RecordStatus
} & ({ managed: false; ownerTeam: string | null; managementHash: string | null } |
  { managed: true; ownerTeam: string; managementHash: string })
export type AdminPage = { items: AdminRecord[]; nextToken: string | null }
export type RegistryConnection = {
  registryArn: string
  region: string
  authorization: AuthorizerType | null
  connection: { state: 'CONNECTED' } | { state: 'ERROR'; code: string; message: string }
  autoApproval: { state: AutoApprovalState }
  warnings: string[]
}
export type OwnershipRequest = { ownerTeam: string; reason: string }
export type AdministrationResult = {
  recordId: string
  ownerTeam: string
  operationId: string
  operationState: 'SUCCEEDED'
  publishedVersion: string | null
  a2aBackfillRequired: boolean
}
export type AdministrationApi = {
  listAdminRecords: (filter: ManagementFilter, nextToken?: string) => Promise<AdminPage>
  getRegistryConnection: () => Promise<RegistryConnection>
  adoptRecord: (recordId: string, request: OwnershipRequest, key: string) => Promise<AdministrationResult>
  reassignOwner: (recordId: string, request: OwnershipRequest & { expectedManagementHash: string }, key: string) => Promise<AdministrationResult>
}

function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}
function isAdminRecord(value: unknown): value is AdminRecord {
  return isObject(value) && hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) && isOneOf(AWS_RECORD_STATUSES, value.status) &&
    ((value.managed === true && typeof value.ownerTeam === 'string' && typeof value.managementHash === 'string') ||
      (value.managed === false && nullableString(value.ownerTeam) && nullableString(value.managementHash)))
}
function isAdminPage(value: unknown): value is AdminPage {
  return isObject(value) && Array.isArray(value.items) && value.items.every(isAdminRecord) && nullableString(value.nextToken)
}
function isConnection(value: unknown): value is RegistryConnection {
  if (!isObject(value) || !hasStrings(value, ['registryArn', 'region']) ||
    !(value.authorization === null || isOneOf(AUTHORIZER_TYPES, value.authorization)) ||
    !isObject(value.connection) || !isObject(value.autoApproval) ||
    !isOneOf(AUTO_APPROVAL_STATES, value.autoApproval.state) ||
    !Array.isArray(value.warnings) || !value.warnings.every((warning) => typeof warning === 'string')) return false
  return value.connection.state === 'CONNECTED' ||
    (value.connection.state === 'ERROR' && hasStrings(value.connection, ['code', 'message']))
}
function isResult(value: unknown): value is AdministrationResult {
  return isObject(value) && hasStrings(value, ['recordId', 'ownerTeam', 'operationId']) &&
    value.operationState === 'SUCCEEDED' && nullableString(value.publishedVersion) && typeof value.a2aBackfillRequired === 'boolean'
}

export function createAdministrationApi(request: ApiRequest, invalid: () => Error): AdministrationApi {
  async function mutate(path: string, payload: OwnershipRequest, key: string) {
    const body = await request<unknown>('POST', path, payload, { 'idempotency-key': key })
    if (!isResult(body)) throw invalid()
    return body
  }
  return {
    listAdminRecords: async (filter, nextToken) => {
      const query = new URLSearchParams({ management: filter })
      if (nextToken !== undefined) query.set('nextToken', nextToken)
      const body = await request<unknown>('GET', `/api/admin/records?${query}`)
      if (!isAdminPage(body)) throw invalid()
      return body
    },
    getRegistryConnection: async () => {
      const body = await request<unknown>('GET', '/api/admin/connection')
      if (!isConnection(body)) throw invalid()
      return body
    },
    adoptRecord: (recordId, payload, key) => mutate(`/api/records/${encodeURIComponent(recordId)}/adopt`, payload, key),
    reassignOwner: (recordId, payload, key) => mutate(`/api/records/${encodeURIComponent(recordId)}/reassign-owner`, payload, key),
  }
}
