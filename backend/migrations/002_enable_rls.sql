-- As políticas por usuário serão adicionadas com a autenticação.
-- Sem políticas, papéis sujeitos a RLS não podem acessar linhas.
ALTER TABLE ada.agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.agent_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ada.schema_migrations ENABLE ROW LEVEL SECURITY;
