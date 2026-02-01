"""Browser localStorage management for persistent auth sessions."""
import json
import streamlit as st
import streamlit.components.v1 as components


def inject_storage_js():
    """Inject JavaScript to manage localStorage read/write."""
    components.html(
        """
        <script>
        // Expose localStorage to Streamlit via window object
        window.getStorageItem = function(key) {
            return localStorage.getItem(key);
        };
        
        window.setStorageItem = function(key, value) {
            localStorage.setItem(key, value);
        };
        
        window.removeStorageItem = function(key) {
            localStorage.removeItem(key);
        };
        
        window.clearStorage = function() {
            localStorage.clear();
        };
        </script>
        """,
        height=0,
    )


def save_auth_to_storage(session: dict, user: dict, remember_me: bool = True):
    """Save auth session to browser localStorage (if remember_me is True)."""
    if not remember_me:
        return
    
    try:
        # Store as JSON strings
        st.session_state['_storage_session'] = json.dumps(session) if session else None
        st.session_state['_storage_user'] = json.dumps(user) if user else None
        st.session_state['_storage_remember'] = True
        
        # Inject JS to persist to localStorage
        components.html(
            f"""
            <script>
            localStorage.setItem('_auth_session', '{json.dumps(session) if session else "null"}');
            localStorage.setItem('_auth_user', '{json.dumps(user) if user else "null"}');
            localStorage.setItem('_auth_remember', 'true');
            </script>
            """,
            height=0,
        )
    except Exception as e:
        print(f"Failed to save auth to storage: {e}")


def restore_auth_from_storage() -> tuple[dict | None, dict | None]:
    """Restore auth session from browser localStorage."""
    try:
        # Try to read from Streamlit session state first (faster)
        if '_storage_remember' in st.session_state:
            session_json = st.session_state.get('_storage_session')
            user_json = st.session_state.get('_storage_user')
            
            if session_json:
                session = json.loads(session_json)
                user = json.loads(user_json) if user_json else None
                return session, user
        
        return None, None
    except Exception as e:
        print(f"Failed to restore auth from storage: {e}")
        return None, None


def clear_auth_storage():
    """Clear all auth data from localStorage."""
    try:
        st.session_state.pop('_storage_session', None)
        st.session_state.pop('_storage_user', None)
        st.session_state.pop('_storage_remember', None)
        
        # Inject JS to clear localStorage
        components.html(
            """
            <script>
            localStorage.removeItem('_auth_session');
            localStorage.removeItem('_auth_user');
            localStorage.removeItem('_auth_remember');
            </script>
            """,
            height=0,
        )
    except Exception as e:
        print(f"Failed to clear auth storage: {e}")
