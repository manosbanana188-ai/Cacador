# 🎯 Caçador de Clientes

Página com chat de IA: você escreve "dentistas sem site num raio de 15 km de Curitiba" e ele busca
no Google, mostrando quem **não tem site**, quem tem só Instagram/Facebook/diretório e quem tem site próprio.
Também há campos manuais (nichos, local, raio em km, estado/país inteiro, filtros) e exportação CSV/Excel.

## Chaves necessárias
| Chave | Para quê | Onde criar |
|---|---|---|
| Google Places API (New) | buscar os negócios | console.cloud.google.com → ative a "Places API (New)" + faturamento → Credenciais → Criar chave |
| Anthropic | o chat entender seu pedido (opcional) | console.anthropic.com → API keys |
| APP_PASSWORD | senha para abrir a página (você escolhe) | — |

Telefone e site são campos pagos no Google (há cota gratuita mensal; confira os preços atuais).
O "Limite de requisições" da barra lateral é a trava de custo.

## Publicar com link próprio (Streamlit Community Cloud, grátis)
1. Crie uma conta em github.com e um repositório **privado** novo (ex.: `cacador`).
2. Envie todos os arquivos desta pasta para ele (botão *Add file → Upload files*).
   O `.streamlit/secrets.toml.example` vai junto; o `secrets.toml` real NUNCA.
3. Entre em share.streamlit.io com o GitHub → *Create app* → escolha o repositório, branch `main`,
   arquivo `app.py`.
4. Em *Advanced settings → Secrets*, cole o conteúdo de `secrets.toml.example` com suas chaves e a senha.
5. *Deploy*. Em ~2 min você recebe um link `https://….streamlit.app`.

**Importante:** como as chaves ficam no servidor, quem abrir o link gasta o seu crédito. Por isso
defina sempre o `APP_PASSWORD` (ou deixe o app privado em *Share* e convide só e-mails seus).

## Rodar no seu computador
```bash
pip install -r requirements.txt
streamlit run app.py
```
(chaves coladas na barra lateral ou em variáveis de ambiente)

## Como funciona
- **Cidade + raio**: geocodifica o local (OpenStreetMap), busca em círculo e descarta quem está além
  do raio. Acima de 50 km o raio é coberto por vários círculos de 50 km.
- **Estado/país**: varre a região com uma grade de círculos de 50 km.
- O Google entrega no máximo ~60 resultados por busca/ponto; em áreas densas use raios menores
  ou várias cidades.
- A IA só configura a busca; os dados vêm do Google, nunca da IA.

## Testes
`python -m pytest`

## Uso responsável
Contato comercial legítimo, respeitando a LGPD e os termos do Google Maps Platform.
