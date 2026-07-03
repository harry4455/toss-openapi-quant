"""MCP 서버 런처 (프로젝트 루트). Claude 설정에서 이 파일을 실행한다.

  python run_mcp.py
src 패키지의 상대 임포트가 동작하도록 루트에서 실행하는 진입점.
"""

from src.mcp_server import main

if __name__ == "__main__":
    main()
