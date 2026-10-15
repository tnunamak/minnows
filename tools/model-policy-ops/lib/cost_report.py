"""Read-only decision cost reconciliation.

Implementation boundary: current explicit closes and unique observed usage are
facts about delegations. They do not establish whole-task acceptance or cost.
No report operation may write or strengthen a receipt, close, or observation.
"""
