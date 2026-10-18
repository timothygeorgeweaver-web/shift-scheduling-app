"""Canonical security inventories without passwords or session secrets."""

import json


def security_inventory(database, admin):
    return json.loads(admin("""
        BEGIN READ ONLY;
        SELECT jsonb_build_object(
          'database_owner', (
            SELECT pg_get_userbyid(datdba)
            FROM pg_database WHERE datname=current_database()
          ),
          'database_acl', (
            SELECT jsonb_agg(jsonb_build_array(
              pg_get_userbyid(a.grantor),
              CASE WHEN a.grantee=0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(a.grantee) END,
              a.privilege_type,a.is_grantable
            ) ORDER BY pg_get_userbyid(a.grantor),
                       pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
            FROM pg_database d
            CROSS JOIN LATERAL aclexplode(
              coalesce(d.datacl,acldefault('d',d.datdba))
            ) a WHERE d.datname=current_database()
          ),
          'schema_acl', (
            SELECT jsonb_agg(jsonb_build_array(
              pg_get_userbyid(a.grantor),
              CASE WHEN a.grantee=0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(a.grantee) END,
              a.privilege_type,a.is_grantable
            ) ORDER BY pg_get_userbyid(a.grantor),
                       pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
            FROM pg_namespace n
            CROSS JOIN LATERAL aclexplode(
              coalesce(n.nspacl,acldefault('n',n.nspowner))
            ) a WHERE n.nspname='scheduling'
          ),
          'row_security', (
            SELECT jsonb_agg(jsonb_build_array(
              c.relname,c.relrowsecurity,c.relforcerowsecurity
            ) ORDER BY c.relname)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='scheduling' AND c.relkind IN ('r','p')
          ),
          'policies', (
            SELECT jsonb_agg(jsonb_build_array(
              tablename,policyname,permissive,roles,cmd,qual,with_check
            ) ORDER BY tablename,policyname)
            FROM pg_policies WHERE schemaname='scheduling'
          ),
          'routines', (
            SELECT jsonb_agg(jsonb_build_object(
              'name',p.proname,
              'arguments',pg_get_function_identity_arguments(p.oid),
              'owner',pg_get_userbyid(p.proowner),
              'definition',pg_get_functiondef(p.oid),
              'acl',(
                SELECT jsonb_agg(jsonb_build_array(
                  pg_get_userbyid(a.grantor),
                  CASE WHEN a.grantee=0 THEN 'PUBLIC'
                       ELSE pg_get_userbyid(a.grantee) END,
                  a.privilege_type,a.is_grantable
                ) ORDER BY pg_get_userbyid(a.grantor),
                           pg_get_userbyid(a.grantee),
                           a.privilege_type,a.is_grantable)
                FROM aclexplode(
                  coalesce(p.proacl,acldefault('f',p.proowner))
                ) a
              )
            ) ORDER BY p.proname,pg_get_function_identity_arguments(p.oid))
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='scheduling' AND p.prokind IN ('f','p')
          ),
          'table_acl', (
            SELECT jsonb_agg(jsonb_build_array(
              c.relname,pg_get_userbyid(a.grantor),
              CASE WHEN a.grantee=0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(a.grantee) END,
              a.privilege_type,a.is_grantable
            ) ORDER BY c.relname,pg_get_userbyid(a.grantor),
                       pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(
              coalesce(c.relacl,acldefault('r',c.relowner))
            ) a
            WHERE n.nspname='scheduling' AND c.relkind IN ('r','p')
          ),
          'column_acl', (
            SELECT jsonb_agg(jsonb_build_array(
              c.relname,t.attname,pg_get_userbyid(a.grantor),
              CASE WHEN a.grantee=0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(a.grantee) END,
              a.privilege_type,a.is_grantable
            ) ORDER BY c.relname,t.attname,pg_get_userbyid(a.grantor),
                       pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute t ON t.attrelid=c.oid
            CROSS JOIN LATERAL aclexplode(t.attacl) a
            WHERE n.nspname='scheduling' AND c.relkind IN ('r','p')
              AND t.attnum>0 AND NOT t.attisdropped
          ),
          'application_roles', (
            SELECT jsonb_agg(jsonb_build_array(
              rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
              rolreplication,rolbypassrls,rolinherit,rolconnlimit
            ) ORDER BY rolname)
            FROM pg_roles
            WHERE rolname IN ('shift_scheduling_owner','shift_scheduling_app')
          ),
          'role_memberships', (
            SELECT jsonb_agg(jsonb_build_array(
              r.rolname,m.rolname,g.rolname,a.admin_option,
              a.inherit_option,a.set_option
            ) ORDER BY r.rolname,m.rolname,g.rolname)
            FROM pg_auth_members a
            JOIN pg_roles r ON r.oid=a.roleid
            JOIN pg_roles m ON m.oid=a.member
            JOIN pg_roles g ON g.oid=a.grantor
            WHERE r.rolname IN ('shift_scheduling_owner','shift_scheduling_app')
               OR m.rolname IN ('shift_scheduling_owner','shift_scheduling_app')
          )
        );
        COMMIT;
    """, database))
