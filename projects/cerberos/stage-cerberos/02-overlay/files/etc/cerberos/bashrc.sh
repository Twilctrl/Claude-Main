# CerberOS shell theme. Sourced from ~/.bashrc for interactive shells.
case $- in *i*) ;; *) return ;; esac

if [ "$(id -u)" -eq 0 ]; then _cb_u='\[\e[1;91m\]'; else _cb_u='\[\e[1;93m\]'; fi
_cb_f='\[\e[31m\]'   # frame: blood red
_cb_r='\[\e[0m\]'
_cb_status() {
    local s=$?
    [ $s -ne 0 ] && printf '\001\e[1;91m\002[%s]\001\e[31m\002─' "$s"
}
PS1="${_cb_f}┌─[${_cb_u}\u${_cb_f}@\[\e[1;31m\]\h${_cb_f}]─[\[\e[33m\]\w${_cb_f}]\n${_cb_f}└─\$(_cb_status)\[\e[1;91m\]»${_cb_r} "
unset _cb_u _cb_f _cb_r

export LS_COLORS='di=1;31:ln=35:so=33:pi=33:ex=1;93:bd=33:cd=33:su=1;91:sg=1;91:tw=1;31:ow=1;31:*.gguf=1;35:*.hef=1;35:*.onnx=1;35:*.json=33:*.toml=33:*.py=93:*.sh=93:*.md=37'
export OLLAMA_HOST=127.0.0.1:11434   # every Ollama client talks to the gate
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias ll='ls -lah'
alias chat='cerb chat'
alias models='cerb models'
alias temp='vcgencmd measure_temp'
